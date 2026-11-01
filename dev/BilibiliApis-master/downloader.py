"""音视频下载器模块：支持指定清晰度、只下载音频、自动混流与元数据同步.
默认下载目录：项目根目录下的 download，可通过 BILI_DATA_DIR 覆盖。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
import urllib.request

def _resolve_ffmpeg_bin() -> str:
    exe_dir = Path(sys.argv[0]).resolve().parent if sys.argv else Path.cwd()
    module_dir = Path(__file__).resolve().parent
    for candidate in (
        exe_dir / "ffmpeg.exe",
        module_dir / "ffmpeg.exe",
        exe_dir / "imageio_ffmpeg" / "binaries" / "ffmpeg-win-x86_64-v7.1.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    bin_dir = exe_dir / "imageio_ffmpeg" / "binaries"
    if bin_dir.is_dir():
        for p in bin_dir.glob("ffmpeg*.exe"):
            return str(p)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


FFMPEG_BIN = _resolve_ffmpeg_bin()

from builder.header import HeaderBuilder, HeaderType
from apis.bili_apis import BiliApi
from utils.http_util import get_json
from utils.paths import default_data_dir

# 视频画质清晰度常量对照表 (支持最高 8K / 杜比视界 / HDR / 4K / 高帧率 / 高码率)
QUALITY_MAP = {
    "MAX": 127,
    "8K": 127,
    "DOLBY": 126,
    "HDR": 125,
    "4K": 120,
    "1080P60": 116,
    "1080P+": 112,
    "1080P": 80,
    "720P": 64,
    "480P": 32,
    "360P": 16,
}

QUALITY_LABEL_MAP = {
    127: "8K",
    126: "杜比视界",
    125: "HDR",
    120: "4K",
    116: "1080P60",
    112: "1080P+",
    80: "1080P",
    64: "720P",
    32: "480P",
    16: "360P",
}

# 音频音质常量对照表
AUDIO_QUALITY_MAP = {
    "HIRES": 30251,
    "30251": 30251,
    "DOLBY": 30250,
    "30250": 30250,
    "192K": 30280,
    "30280": 30280,
    "132K": 30232,
    "30232": 30232,
    "64K": 30216,
    "30216": 30216,
}


def parse_duration_str(dur_str: str) -> int:
    """将 'MM:SS' 或 'HH:MM:SS' 时长字符串解析为秒数."""
    if not dur_str:
        return 0
    try:
        parts = [int(p) for p in str(dur_str).strip().split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        return int(parts[0])
    except Exception:
        return 0


def sanitize_filename(name: str) -> str:
    """去除 Windows 文件名非法字符."""
    return re.sub(r'[\\/:*?"<>|]', '_', name).strip()


import time
import threading


class DownloadCancelledError(RuntimeError):
    """用户主动取消下载任务时抛出."""
    pass


class RestrictedVideoError(RuntimeError):
    """视频属于充电专属、付费课程/视频或未解锁的大会员试看流时抛出（直接熔断不重试）."""
    def __init__(self, reason: str, message: str = ""):
        self.reason = reason or "付费/受限"
        super().__init__(message or f"受限视频禁止下载 ({self.reason})")


class MediaDownloader:
    def __init__(self, auth, download_dir: str = None):
        self.auth = auth
        self.download_dir = Path(download_dir or default_data_dir())
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def get_cid(self, bvid: str) -> int:
        """获取视频 1P 的 cid."""
        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f"https://www.bilibili.com/video/{bvid}").get()
        url = f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}"
        res = get_json(self.auth, url, headers=headers)
        if res.get('code') == 0:
            data = res.get('data') or []
            if data:
                return data[0].get('cid')
        raise RuntimeError(f"获取视频 cid 失败: bvid={bvid}, msg={res.get('message')}")

    def inspect_video_meta(self, bvid: str, video_info: dict, block_paid: bool = True) -> tuple:
        """
        下载前通过 web-interface/view 校验视频权限（充电专属 / 付费 / 会员剧集），并返回 (cid, duration_seconds).
        """
        if block_paid and (video_info.get("is_restricted") or video_info.get("restricted")):
            reason = str(video_info.get("restrict_reason") or "付费/充电受限")
            raise RestrictedVideoError(reason, f"该视频已被识别为【{reason}】，已禁止下载")

        headers = HeaderBuilder.build(HeaderType.GET).set_referer(f"https://www.bilibili.com/video/{bvid}").get()
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        try:
            res = get_json(self.auth, url, headers=headers)
        except Exception:
            res = {}

        if res.get("code") == 0:
            data = res.get("data") or {}
            rights = data.get("rights") or {}
            redirect_url = str(data.get("redirect_url") or "")

            if block_paid:
                # 1. 充电专属检测
                if (
                    data.get("is_upower_exclusive") in (1, True)
                    or data.get("up_ower_exclusive") in (1, True)
                    or rights.get("is_upower_exclusive") in (1, True)
                    or int(data.get("elec_arc_type") or 0) > 0
                ):
                    raise RestrictedVideoError("充电专属", "该视频为 UP 主【充电专属】视频，已自动拦截")

                # 2. 付费视频 / 课堂 / 付费合集检测
                if (
                    rights.get("pay") == 1
                    or rights.get("arc_pay") == 1
                    or rights.get("ugcpay") == 1
                    or rights.get("pay_free_watch") == 1
                    or "cheese/play" in redirect_url
                ):
                    raise RestrictedVideoError("付费视频", "该视频为【付费/课堂】专享内容，已自动拦截")

                # 3. 番剧/影视大会员重定向检测
                if "bangumi/play" in redirect_url:
                    raise RestrictedVideoError("会员番剧/影视", "该视频为【大会员番剧/影视】内容，已自动拦截")

                # 4. 互动视频检测
                if rights.get("is_stein_gate") == 1:
                    raise RestrictedVideoError("互动视频", "该视频为特殊【互动视频】，不支持常规流下载")

            cid = video_info.get("cid") or data.get("cid")
            pages = data.get("pages") or []
            expected_dur = int(data.get("duration") or 0)
            if pages:
                if not cid:
                    cid = pages[0].get("cid")
                for p in pages:
                    if p.get("cid") == cid and p.get("duration"):
                        expected_dur = int(p.get("duration") or expected_dur)
                        break
            if cid:
                return int(cid), expected_dur

        cid = video_info.get("cid") or self.get_cid(bvid)
        fallback_dur = parse_duration_str(video_info.get("duration") or video_info.get("时长") or "")
        return int(cid), fallback_dur

    @staticmethod
    def _select_audio_stream(dash: dict, target_audio_qn: int) -> tuple:
        """
        根据用户选择的音频质量从 DASH 字典中匹配最佳音轨，支持:
        - 30251: Hi-Res 无损 (dash.flac.audio)
        - 30250: 杜比全景声 (dash.dolby.audio)
        - 30280: 192K 高品质
        - 30232: 132K 标准
        - 30216: 64K 省流
        若目标高阶音轨不存在，自动向下降级到最高可用标准音轨。
        """
        # 1. Hi-Res 无损优先
        if target_audio_qn >= 30251:
            flac_info = dash.get("flac") or {}
            flac_audio = flac_info.get("audio") if isinstance(flac_info, dict) else None
            if isinstance(flac_audio, dict):
                u = flac_audio.get("baseUrl") or flac_audio.get("base_url") or (flac_audio.get("backupUrl") or [None])[0]
                if u:
                    return u, "30251 (Hi-Res无损)"

        # 2. 杜比全景声 (或从 30251 降级尝试)
        if target_audio_qn >= 30250:
            dolby_info = dash.get("dolby") or {}
            dolby_audios = dolby_info.get("audio") if isinstance(dolby_info, dict) else None
            if isinstance(dolby_audios, list) and dolby_audios:
                da = dolby_audios[0]
                u = da.get("baseUrl") or da.get("base_url") or (da.get("backupUrl") or [None])[0]
                if u:
                    return u, "30250 (杜比全景声)"
            elif isinstance(dolby_audios, dict):
                u = dolby_audios.get("baseUrl") or dolby_audios.get("base_url") or (dolby_audios.get("backupUrl") or [None])[0]
                if u:
                    return u, "30250 (杜比全景声)"

        # 3. 常规 AAC/M4A 音轨 (30280 / 30232 / 30216)
        audios = dash.get("audio") or []
        if not audios:
            raise RuntimeError("未找到可用音频流")

        std_target = min(target_audio_qn, 30280) if target_audio_qn >= 30250 else target_audio_qn
        chosen = None
        for a in audios:
            if a.get("id") == std_target:
                chosen = a
                break
        if not chosen:
            le_list = [a for a in audios if int(a.get("id") or 0) <= std_target]
            if le_list:
                chosen = sorted(le_list, key=lambda x: (int(x.get("id") or 0), int(x.get("bandwidth") or 0)), reverse=True)[0]
            else:
                chosen = sorted(audios, key=lambda x: int(x.get("bandwidth") or 0), reverse=True)[0]

        audio_url = chosen.get("baseUrl") or chosen.get("base_url") or (chosen.get("backupUrl") or [None])[0]
        return audio_url, str(chosen.get("id") or "audio")

    def download_stream(self, stream_url: str, output_path: Path, bvid: str, progress_callback=None, cancel_event: threading.Event = None):
        """流式分片下载，支持实时速度回调与即时取消中断."""
        req = urllib.request.Request(stream_url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
        req.add_header('Referer', f'https://www.bilibili.com/video/{bvid}')

        try:
            with urllib.request.urlopen(req, timeout=30) as response, open(output_path, 'wb') as out_file:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                chunk_size = 256 * 1024  # 256KB
                last_report = time.time()
                last_bytes = 0

                while True:
                    if cancel_event and cancel_event.is_set():
                        raise DownloadCancelledError("下载已被用户取消")
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if (now - last_report >= 0.4) or (total_size > 0 and downloaded >= total_size):
                        speed_bps = (downloaded - last_bytes) / max(0.001, (now - last_report))
                        speed_str = f"{speed_bps / (1024 * 1024):.1f} MB/s" if speed_bps >= 1024 * 1024 else f"{speed_bps / 1024:.0f} KB/s"
                        percent = round((downloaded / total_size) * 100, 1) if total_size > 0 else 0
                        if progress_callback:
                            progress_callback(percent, speed_str, downloaded, total_size)
                        last_report = now
                        last_bytes = downloaded
        except BaseException:
            if output_path.exists():
                try:
                    output_path.unlink()
                except Exception:
                    pass
            raise

    def download(
        self,
        video_info: dict,
        quality: str = "1080P",
        audio_only: bool = False,
        audio_quality: str = "30280",
        block_paid: bool = True,
        progress_callback=None,
        cancel_event: threading.Event = None,
    ) -> str:
        """根据配置下载视频或仅音频，并在同级生成配套元数据 JSON.

        :param video_info: 包含 bvid, title/视频名字, author/UP名字, pubdate/发布日期 等基础信息的字典
        :param quality: 视频画质（MAX/8K/DOLBY/HDR/4K/1080P60/1080P+/1080P/720P/480P/360P）
        :param audio_only: True 则只下载原生音频（转写/知识库极佳），False 下载视频
        :param audio_quality: 音频质量（30251 Hi-Res / 30250 杜比全景声 / 30280 192K / 30232 132K / 30216 64K）
        :param block_paid: 是否自动拦截充电专属、付费课程及会员试看视频
        :param cancel_event: 取消信号事件
        :return: 最终保存的文件绝对路径
        """
        if cancel_event and cancel_event.is_set():
            raise DownloadCancelledError("下载已被用户取消")

        raw_bvid = video_info.get("bvid", "")
        bvid = video_info.get("real_bvid") or raw_bvid.split("_P")[0]
        raw_title = video_info.get("title") or video_info.get("视频名字") or bvid
        raw_author = video_info.get("author") or video_info.get("UP名字") or "未知UP主"
        raw_pubdate = video_info.get("pubdate") or video_info.get("发布日期") or ""

        title = sanitize_filename(raw_title)
        author = sanitize_filename(raw_author)
        pubdate = (raw_pubdate[:10]).replace("-", "")

        # 1. 下载前执行 view 详情鉴权与付费/充电专属拦截，并获取 cid 和真实总时长
        cid, expected_dur_sec = self.inspect_video_meta(bvid, video_info, block_paid=block_paid)

        # 为该 UP 主创建独立归档目录
        bili_root = self.download_dir if self.download_dir.name.lower() == "bilibili" else (self.download_dir / "bilibili")
        up_dir = bili_root / author
        up_dir.mkdir(parents=True, exist_ok=True)

        q_key = str(quality or "1080P").strip().upper()
        target_qn = QUALITY_MAP.get(q_key, int(q_key) if q_key.isdigit() else 80)
        aq_key = str(audio_quality or "30280").strip().upper()
        target_audio_qn = AUDIO_QUALITY_MAP.get(aq_key, int(aq_key) if aq_key.isdigit() else 30280)

        # 请求 DASH 播放流 (fnval=4048 开启全部 8K/杜比/HDR/4K/Hi-Res 流支持)
        play_data = BiliApi.get_play_url(self.auth, bvid=bvid, cid=cid, qn=target_qn, fnval=4048)
        if play_data.get('code') != 0:
            err_msg = str(play_data.get('message') or '')
            if block_paid and any(k in err_msg for k in ("充电", "付费", "购买", "会员", "专享", "试看", "权限")):
                raise RestrictedVideoError("付费/会员受限", f"B站拒绝返回完整流: {err_msg}")
            raise RuntimeError(f"获取播放地址失败: {err_msg}")

        play_info = play_data.get('data') or {}
        dash = play_info.get('dash') or {}

        # 2. 第二道防线：PlayURL 试看标记与试看片段时长比对拦截
        if block_paid:
            if play_info.get("is_preview") == 1:
                raise RestrictedVideoError("试看专享", "检测到该视频仅返回试看流 (充电专属/付费/大会员专享)，已禁止下载")

            timelength_ms = int(play_info.get("timelength") or 0)
            dash_dur_sec = int(dash.get("duration") or 0)
            actual_stream_sec = dash_dur_sec if dash_dur_sec > 0 else (timelength_ms / 1000.0 if timelength_ms > 0 else 0)
            if expected_dur_sec > 45 and 0 < actual_stream_sec <= 42 and actual_stream_sec < expected_dur_sec * 0.5:
                raise RestrictedVideoError(
                    "试看片段拦截",
                    f"检测到试看片段 (原片 {expected_dur_sec}s，实际仅返回 {int(actual_stream_sec)}s 试看流)，属于充电专属/付费/未解锁会员视频，已拦截"
                )
            if not dash and play_info.get("durl"):
                # 某些充电专属/受限视频仅返回一段极短 durl 试看流
                durl_len_ms = sum(int(d.get("length") or 0) for d in (play_info.get("durl") or []))
                durl_sec = durl_len_ms / 1000.0 if durl_len_ms > 0 else actual_stream_sec
                if expected_dur_sec > 45 and 0 < durl_sec <= 42 and durl_sec < expected_dur_sec * 0.5:
                    raise RestrictedVideoError("试看片段拦截", "检测到短试看流 (充电专属/付费视频)，已禁止下载")

        videos = dash.get('video') or []
        audio_url, matched_audio_label = self._select_audio_stream(dash, target_audio_qn)

        part_suffix = f"_P{video_info.get('part_page')}" if video_info.get("part_page") else ""
        base_name = f"{pubdate}_{title}_{bvid}{part_suffix}" if pubdate else f"{title}_{bvid}{part_suffix}"
        run_tag = f"{os.getpid()}_{threading.get_ident()}_{int(time.time() * 1000)}"

        # ---------------------------------------------------- 1. 仅下载音频（先写临时文件，成功后再原子替换最终文件）
        if audio_only:
            audio_path = up_dir / f"{base_name}.m4a"
            temp_audio_only = up_dir / f".temp_{base_name}_{run_tag}.m4a.part"
            temp_meta = up_dir / f".temp_{base_name}_{run_tag}.json.part"
            print(f"    [*] 正在下载音频 [{matched_audio_label}]: {audio_path.name} ...")
            try:
                self.download_stream(audio_url, temp_audio_only, bvid=bvid, progress_callback=progress_callback, cancel_event=cancel_event)
                if cancel_event and cancel_event.is_set():
                    raise DownloadCancelledError("下载已被用户取消")
                if not temp_audio_only.exists() or temp_audio_only.stat().st_size <= 0:
                    raise RuntimeError("音频下载失败：临时文件为空")
                os.replace(temp_audio_only, audio_path)

                # 写入配套知识库元数据（临时文件原子替换）
                meta_path = up_dir / f"{base_name}.json"
                meta_info = dict(video_info)
                meta_info["media_type"] = "audio"
                meta_info["audio_quality"] = matched_audio_label
                meta_info["file_path"] = str(audio_path.resolve())
                with open(temp_meta, "w", encoding="utf-8") as f:
                    json.dump(meta_info, f, ensure_ascii=False, indent=2)
                os.replace(temp_meta, meta_path)
            finally:
                for tp in (temp_audio_only, temp_meta):
                    if tp.exists():
                        try:
                            tp.unlink()
                        except Exception:
                            pass

            return str(audio_path)

        # ---------------------------------------------------- 2. 下载音视频并混流（先写临时文件，混流日志重定向至文件避免管道阻塞）
        if not videos:
            raise RuntimeError("未找到可用视频流")

        # 寻找与目标画质最匹配的视频轨（同画质下优先选带宽更高的编码流）
        exact_matches = [v for v in videos if v.get('id') == target_qn]
        if exact_matches:
            target_video = sorted(exact_matches, key=lambda x: int(x.get('bandwidth') or 0), reverse=True)[0]
        else:
            # 没有找到完全一致的 qn 时，降级选择小于等于目标的最大画质
            lower_matches = sorted(
                [v for v in videos if int(v.get('id') or 0) <= target_qn],
                key=lambda x: (int(x.get('id') or 0), int(x.get('bandwidth') or 0)),
                reverse=True,
            )
            target_video = lower_matches[0] if lower_matches else videos[0]

        actual_qn = int(target_video.get("id") or target_qn)
        actual_quality_label = QUALITY_LABEL_MAP.get(actual_qn, quality)
        video_url = target_video.get('baseUrl') or target_video.get('base_url') or target_video.get('backupUrl', [None])[0]

        temp_video = up_dir / f".temp_{base_name}_{run_tag}.m4v.part"
        temp_audio = up_dir / f".temp_{base_name}_{run_tag}.m4a.part"
        temp_mux_mp4 = up_dir / f".temp_{base_name}_{run_tag}.mux.mp4"
        ffmpeg_log_path = up_dir / f".temp_{base_name}_{run_tag}.ffmpeg.log"
        temp_meta = up_dir / f".temp_{base_name}_{run_tag}.json.part"
        final_mp4 = up_dir / f"{base_name}.mp4"

        try:
            print(f"    [*] 正在下载视频流 (目标:{quality} -> 实际:{actual_quality_label}): {title} ...")
            self.download_stream(video_url, temp_video, bvid=bvid, progress_callback=progress_callback, cancel_event=cancel_event)

            print(f"    [*] 正在下载音频流 ({matched_audio_label}): {title} ...")
            self.download_stream(audio_url, temp_audio, bvid=bvid, cancel_event=cancel_event)

            if cancel_event and cancel_event.is_set():
                raise DownloadCancelledError("下载已被用户取消")

            print(f"    [*] 正在合并音视频轨 (-c copy) ...")
            cmd = [
                FFMPEG_BIN, "-y",
                "-i", str(temp_video),
                "-i", str(temp_audio),
                "-c", "copy",
                "-f", "mp4",
                str(temp_mux_mp4)
            ]
            with open(ffmpeg_log_path, "w", encoding="utf-8", errors="replace") as ff_log:
                proc = subprocess.Popen(cmd, stdout=ff_log, stderr=subprocess.STDOUT)
                while proc.poll() is None:
                    if cancel_event and cancel_event.is_set():
                        proc.kill()
                        proc.wait()
                        raise DownloadCancelledError("混流阶段被用户取消")
                    time.sleep(0.2)

            if proc.returncode != 0:
                stderr_out = ""
                if ffmpeg_log_path.exists():
                    try:
                        stderr_out = ffmpeg_log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                    except Exception:
                        pass
                raise RuntimeError(f"FFmpeg 混流失败: {stderr_out}")

            if cancel_event and cancel_event.is_set():
                raise DownloadCancelledError("混流阶段被用户取消")
            if not temp_mux_mp4.exists() or temp_mux_mp4.stat().st_size <= 0:
                raise RuntimeError("FFmpeg 混流输出文件为空")

            os.replace(temp_mux_mp4, final_mp4)

            # 写入配套元数据 JSON（临时文件原子替换）
            meta_path = up_dir / f"{base_name}.json"
            meta_info = dict(video_info)
            meta_info["media_type"] = "video"
            meta_info["quality"] = actual_quality_label
            meta_info["audio_quality"] = matched_audio_label
            meta_info["file_path"] = str(final_mp4.resolve())
            with open(temp_meta, "w", encoding="utf-8") as f:
                json.dump(meta_info, f, ensure_ascii=False, indent=2)
            os.replace(temp_meta, meta_path)
        finally:
            for tp in (temp_video, temp_audio, temp_mux_mp4, ffmpeg_log_path, temp_meta):
                if tp.exists():
                    try:
                        tp.unlink()
                    except Exception:
                        pass

        return str(final_mp4)
