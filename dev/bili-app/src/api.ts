/**
 * 后端 API 接口封装 (支持 Tauri 2 动态端口与 Token 握手)
 */

import { ref } from "vue";
import { invoke } from "@tauri-apps/api/core";

export const apiBase = ref<string>("http://127.0.0.1:18888");
export const authToken = ref<string>("");
export const API_BASE = "http://127.0.0.1:18888"; // 兼容导出

// 全局响应式后端健康状态
export const serverOnline = ref<boolean>(false);
export const serverStatus = ref<string>("STARTING");

/**
 * 初始化与 Tauri 主进程的连接，获取动态分配的端口与凭据
 */
let tauriHandshakeDone = false;

async function ensureTauriServerInfo(): Promise<void> {
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window && !tauriHandshakeDone) {
    try {
      const info = await invoke<{ port: number; token: string; status: string }>("get_server_info");
      if (info && info.port) {
        apiBase.value = `http://127.0.0.1:${info.port}`;
        authToken.value = info.token || "";
        serverStatus.value = info.status || "STARTING";
        tauriHandshakeDone = true;
      }
    } catch (e) {
      console.warn("获取 Tauri 后端服务信息失败，使用默认端口回退:", e);
    }
  }
}

export async function initServerConnection(): Promise<boolean> {
  tauriHandshakeDone = false;
  await ensureTauriServerInfo();
  return await checkServerHealth();
}

export async function checkServerHealth(): Promise<boolean> {
  await ensureTauriServerInfo();
  const headers: Record<string, string> = {};
  if (authToken.value) {
    headers["X-Auth-Token"] = authToken.value;
  }
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);
    const res = await fetch(`${apiBase.value}/api/health`, {
      headers,
      signal: controller.signal
    });
    clearTimeout(timeoutId);
    if (res.ok) {
      serverOnline.value = true;
      serverStatus.value = "READY";
      return true;
    }
  } catch (_) {
    // 降级尝试原 /api/auth/status
    try {
      const res2 = await fetch(`${apiBase.value}/api/auth/status`, { headers });
      if (res2.ok) {
        serverOnline.value = true;
        serverStatus.value = "READY";
        return true;
      }
    } catch {
      serverOnline.value = false;
    }
  }
  return false;
}

export interface UserInfo {
  is_login: boolean;
  mid?: string | number;
  uname?: string;
  face?: string;
  vip_type?: number;
  message?: string;
}

export interface RelationTag {
  tagid: number;
  name: string;
  count: number;
  tip?: string;
}

export interface VideoItem {
  bvid: string;
  real_bvid?: string;
  cid?: number;
  part_page?: number;
  part_title?: string;
  aid?: number;
  title?: string;
  author?: string;
  pubdate?: string;
  url?: string;
  up_mid?: number;
  play?: number | string;
  duration?: string;
  pic?: string;
  视频名字: string;
  UP名字: string;
  发布日期: string;
  created: number;
  URL: string;
  UP_MID: number;
  播放量?: number | string;
  时长?: string;
  封面?: string;
  is_read?: number;
  is_restricted?: number;
  restrict_reason?: string;
  download_status?: string;
  local_file?: string;
}

export interface AutoCheckStatus {
  is_running: boolean;
  last_check_time: number;
  last_new_count: number;
  total_new_found: number;
  unread_count?: number;
  last_message: string;
}

export interface SyncStatusInfo {
  is_syncing: boolean;
  scope: string;
  current: number;
  total: number;
  message: string;
}

export async function fetchApi<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  if (!tauriHandshakeDone) {
    await ensureTauriServerInfo();
  }
  const url = `${apiBase.value}${endpoint}`;
  const method = options.method || "GET";
  const isPoll = endpoint.includes("/api/download/tasks") || endpoint.includes("/api/limiter/status") || endpoint.includes("/api/health");

  let parsedBody: any = "";
  if (options.body && typeof options.body === "string") {
    try {
      parsedBody = JSON.parse(options.body);
    } catch {
      parsedBody = options.body;
    }
  }

  if (!isPoll) {
    console.log(`%c[API REQ] ${method} ${endpoint}`, "color: #3b82f6; font-weight: bold;", parsedBody);
  }

  const reqHeaders: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {})
  };
  if (authToken.value) {
    reqHeaders["X-Auth-Token"] = authToken.value;
  }

  try {
    const res = await fetch(url, {
      ...options,
      headers: reqHeaders
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({ detail: res.statusText }));
      console.error(`%c[API ERR] ${method} ${endpoint} [${res.status}]:`, "color: #ef4444; font-weight: bold;", errData);
      throw new Error(errData.detail || `HTTP Error ${res.status}`);
    }

    const data = (await res.json()) as T;
    serverOnline.value = true;
    if (!isPoll) {
      console.log(`%c[API OK]  ${method} ${endpoint}`, "color: #10b981; font-weight: bold;", data);
    }
    return data;
  } catch (err: any) {
    if (err instanceof TypeError && (err.message.includes("fetch") || err.message.includes("network") || err.message.includes("Failed"))) {
      serverOnline.value = false;
    }
    if (!isPoll) {
      console.error(`%c[API FAIL] ${method} ${endpoint}:`, "color: #ef4444; font-weight: bold;", err);
    }
    throw err;
  }
}

// -------------------------------- 鉴权相关
export const apiGetAuthStatus = () => fetchApi<UserInfo>("/api/auth/status");

export const apiImportCookie = (cookie: string) =>
  fetchApi<{ success: boolean; uname: string; face: string; message: string }>("/api/auth/import_cookie", {
    method: "POST",
    body: JSON.stringify({ cookie })
  });

export const apiGenerateQrCode = () =>
  fetchApi<{ success: boolean; qrcode_key: string; qrcode_url: string; qrcode_img: string }>("/api/auth/qrcode/generate", {
    method: "POST"
  });

export const apiPollQrCode = (qrcode_key: string) =>
  fetchApi<{ status: "waiting" | "scanned" | "success" | "expired" | "error"; message: string; uname?: string; face?: string }>("/api/auth/qrcode/poll", {
    method: "POST",
    body: JSON.stringify({ qrcode_key })
  });

export const apiLogout = () =>
  fetchApi<{ success: boolean; message: string }>("/api/auth/logout", {
    method: "POST"
  });

// -------------------------------- 关注与分组
export const apiGetTags = () =>
  fetchApi<{ tags: RelationTag[] }>("/api/relation/tags");

export const apiGetFollowings = (tagid: number = 0, all: boolean = true, localOnly: boolean = false) =>
  fetchApi<{ total: number; items: any[] }>(`/api/relation/followings?tagid=${tagid}&all_followings=${all}&local_only=${localOnly}`);

export const apiSubscribeLocalUp = (inputText: string, tagid: number = -10) =>
  fetchApi<{ success: boolean; up: any; message: string }>("/api/relation/subscribe_local", {
    method: "POST",
    body: JSON.stringify({ input_text: inputText, tagid })
  });

export interface BiliCollection {
  id: number;
  mid: number;
  collection_type: "season" | "series";
  collection_id: number;
  title: string;
  cover: string;
  total: number;
  is_monitored: number;
  last_sync_time?: number;
  updated_at?: number;
}

export const apiGetUpCollections = (mid: number, forceRefresh: boolean = false) =>
  fetchApi<{ success: boolean; mid: number; up_monitored: boolean; collections: BiliCollection[] }>(
    `/api/relation/up_collections?mid=${mid}&force_refresh=${forceRefresh}`
  );

export const apiToggleCollectionMonitor = (
  mid: number,
  collectionType: string,
  collectionId: number,
  isMonitored: boolean
) =>
  fetchApi<{ success: boolean; is_monitored: boolean }>("/api/relation/toggle_collection_monitor", {
    method: "POST",
    body: JSON.stringify({
      mid,
      collection_type: collectionType,
      collection_id: collectionId,
      is_monitored: isMonitored,
    }),
  });

export const apiToggleUpMonitor = (mid: number, isMonitored: boolean) =>
  fetchApi<{ success: boolean; is_monitored: boolean }>("/api/relation/toggle_up_monitor", {
    method: "POST",
    body: JSON.stringify({ mid, is_monitored: isMonitored }),
  });

export const apiGetAutoCheckStatus = () =>
  fetchApi<AutoCheckStatus>("/api/autocheck/status");

export const apiRunAutoCheckNow = () =>
  fetchApi<{ success: boolean; new_count?: number; message: string; state: AutoCheckStatus }>("/api/autocheck/run_now", {
    method: "POST"
  });

export const apiMarkVideosRead = (bvid?: string) =>
  fetchApi<{ success: boolean; affected: number; unread_count: number }>("/api/videos/mark_read", {
    method: "POST",
    body: JSON.stringify({ bvid: bvid || null })
  });

// -------------------------------- 视频更新索引 (支持本地 SQLite、搜索与单博主深度历史抓取)
export const apiGetVideosIndex = (
  tagid: number = 0,
  all: boolean = true,
  forceRefresh: boolean = false,
  search: string = "",
  upMid?: number,
  deepScrape: boolean = false,
  maxPages: number = 1,
  localOnly: boolean = false
) => {
  const params = new URLSearchParams({
    tagid: String(tagid),
    all_followings: String(all),
    force_refresh: String(forceRefresh),
    local_only: String(localOnly),
    deep_scrape: String(deepScrape),
    max_pages: String(maxPages),
  });
  if (search && search.trim()) params.append("search", search.trim());
  if (upMid) params.append("up_mid", String(upMid));
  return fetchApi<{ from_cache: boolean; total: number; videos: VideoItem[] }>(
    `/api/videos/index?${params.toString()}`
  );
};

// -------------------------------- 收藏夹 (Favorites)
export interface FavFolder {
  id: number;
  title: string;
  media_count: number;
  cover?: string;
  attr?: number;
  last_sync_time?: number;
  latest_fav_time?: number;
}

export const apiGetFavFolders = (forceRefresh: boolean = false) =>
  fetchApi<{ from_cache: boolean; folders: FavFolder[] }>(`/api/fav/folders?force_refresh=${forceRefresh}`);

export const apiGetFavVideos = (mediaId: number, forceRefresh: boolean = false, search: string = "", localOnly: boolean = false) => {
  const params = new URLSearchParams({
    media_id: String(mediaId),
    force_refresh: String(forceRefresh),
    local_only: String(localOnly),
  });
  if (search && search.trim()) params.append("search", search.trim());
  return fetchApi<{ from_cache: boolean; total: number; videos: VideoItem[] }>(`/api/fav/videos?${params.toString()}`);
};

// -------------------------------- 下载任务与本地交互
export const apiDownloadSingle = (
  video: VideoItem,
  quality: string = "1080P",
  audioOnly: boolean = true,
  audioQuality: string = "30280"
) =>
  fetchApi<{ success: boolean; bvid: string; message: string }>("/api/download/single", {
    method: "POST",
    body: JSON.stringify({ video, quality, audio_only: audioOnly, audio_quality: audioQuality })
  });

export const apiBatchDownload = (
  videos: VideoItem[],
  quality: string = "1080P",
  audioOnly: boolean = true,
  audioQuality: string = "30280"
) =>
  fetchApi<{ success: boolean; count: number; message: string }>("/api/download/batch", {
    method: "POST",
    body: JSON.stringify({ videos, quality, audio_only: audioOnly, audio_quality: audioQuality })
  });

export interface DownloadTaskItem {
  bvid: string;
  title: string;
  author: string;
  platform?: "bili" | "zhihu";
  account_name?: string;
  group_type?: "folder" | "author" | "collection" | "creator" | "upvote" | "single";
  group_name?: string;
  pic?: string;
  quality: string;
  audio_only: boolean;
  audio_quality?: string;
  status: "pending" | "downloading" | "success" | "failed" | "cancelled" | "restricted";
  progress: number;
  speed: string;
  downloaded_bytes: number;
  total_bytes: number;
  file_path: string;
  error?: string | null;
  retries: number;
  max_retries: number;
  created_at: number;
  updated_at: number;
  task_key?: string;
  account_id?: string;
  is_stopping?: boolean;
  can_retry?: boolean;
}

export interface AppSettings {
  download_dir: string;
  download_max_workers: number;
  max_workers?: number;
  refresh_on_select?: boolean;
  auto_check_enabled: boolean;
  auto_check_interval: number;
  auto_check_scope: 'special' | 'all' | 'all_fav';
  auto_download_new: boolean;
  default_video_quality?: string;
  default_audio_quality?: string;
  block_paid_videos?: boolean;
  theme: 'dark' | 'light';
  disclaimer_accepted?: boolean;
  database_file?: string;
}

export const apiGetDownloadTasks = () =>
  fetchApi<{ tasks: DownloadTaskItem[]; active_count: number }>("/api/download/tasks");

export const apiRetryDownload = (
  bvid?: string,
  allFailed: boolean = false,
  bvids?: string[],
  includeCancelled: boolean = false,
  onlyCancelled: boolean = false
) =>
  fetchApi<{ success: boolean; count?: number; message?: string }>("/api/download/retry", {
    method: "POST",
    body: JSON.stringify({
      bvid,
      all_failed: allFailed,
      bvids,
      include_cancelled: includeCancelled,
      only_cancelled: onlyCancelled,
    }),
  });

export const apiCancelDownload = (bvid: string) =>
  fetchApi<{ success: boolean; bvid: string; message?: string }>("/api/download/cancel", {
    method: "POST",
    body: JSON.stringify({ bvid })
  });

export const apiGetSyncStatus = () =>
  fetchApi<SyncStatusInfo>("/api/sync/status");

export const apiClearCompletedDownloads = (
  bvids?: string[],
  statuses?: string[]
) =>
  fetchApi<{ success: boolean; count: number }>("/api/download/clear_completed", {
    method: "POST",
    body: JSON.stringify({ bvids, statuses }),
  });

export const apiReconcileDownloads = () =>
  fetchApi<{ success: boolean; reset_count: number; restored_count: number; message: string }>("/api/download/reconcile", {
    method: "POST"
  });

export const apiGetSettings = () =>
  fetchApi<AppSettings>("/api/settings");

export const apiUpdateSettings = (settings: Partial<AppSettings>) =>
  fetchApi<{ success: boolean; settings: AppSettings }>("/api/settings", {
    method: "POST",
    body: JSON.stringify(settings)
  });

export const apiOpenFolder = (filePath?: string) =>
  fetchApi<{ success: boolean; message?: string }>("/api/open/folder", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath || "" })
  });

export const apiSelectFolder = (initialDir?: string) =>
  fetchApi<{ success: boolean; path: string; cancelled?: boolean; message?: string }>("/api/system/select_folder", {
    method: "POST",
    body: JSON.stringify({ initial_dir: initialDir || "" })
  });

export const apiOpenFile = (filePath: string) =>
  fetchApi<{ success: boolean; message?: string }>("/api/open/file", {
    method: "POST",
    body: JSON.stringify({ file_path: filePath })
  });

export interface UpdateCheckResult {
  success: boolean;
  has_update: boolean;
  current_version: string;
  latest_version?: string;
  os_platform?: string;
  arch?: string;
  asset_name?: string;
  asset_size?: number;
  release_name?: string;
  changelog?: string;
  published_at?: string;
  download_url?: string;
  html_url?: string;
  message?: string;
  checked_at?: number;
}

export interface UpdateDownloadStatus {
  status: "idle" | "downloading" | "completed" | "failed";
  progress: number;
  downloaded_bytes: number;
  total_bytes: number;
  speed_text: string;
  file_name: string;
  file_path: string;
  error: string;
  updated_at: number;
}

export const apiCheckUpdate = (force: boolean = false) =>
  fetchApi<UpdateCheckResult>("/api/system/check_update?force=" + (force ? "true" : "false"));

export const apiStartUpdateDownload = (downloadUrl?: string, fileName?: string, autoLaunch: boolean = true) =>
  fetchApi<{ success: boolean } & UpdateDownloadStatus>("/api/system/update/download", {
    method: "POST",
    body: JSON.stringify({
      download_url: downloadUrl || "",
      file_name: fileName || "",
      auto_launch: autoLaunch,
    }),
  });

export const apiGetUpdateDownloadStatus = () =>
  fetchApi<UpdateDownloadStatus>("/api/system/update/download_status");

export const apiLaunchUpdateInstaller = (filePath?: string, revealInFolder: boolean = false) =>
  fetchApi<{ success: boolean; message: string; file_path: string }>("/api/system/update/launch_installer", {
    method: "POST",
    body: JSON.stringify({
      file_path: filePath || "",
      reveal_in_folder: revealInFolder,
    }),
  });


/**
 * 格式化图片 URL，支持协议补全与防盗链代理回退
 */
export function formatPicUrl(url?: string): string {
  if (!url) return "https://static.hdslb.com/images/akari.jpg";
  if (url.startsWith("//")) return "https:" + url;
  return url;
}

export function getProxyPicUrl(originalUrl?: string): string {
  if (!originalUrl) return "https://static.hdslb.com/images/akari.jpg";
  const tokenParam = authToken.value ? `&token=${encodeURIComponent(authToken.value)}` : "";
  return `${apiBase.value}/api/proxy/image?url=${encodeURIComponent(formatPicUrl(originalUrl))}${tokenParam}`;
}

// -------------------------------- 接口限流与令牌桶
export interface RateLimiterConfig {
  rate: number;
  capacity: number;
  min_interval: number;
  jitter: number;
  ban_cooldown: number;
  max_retries: number;
  is_paused: boolean;
}

export interface RequestLogItem {
  id: number;
  time: string;
  method: string;
  url: string;
  status_code: number;
  code: number | null;
  duration_ms: number;
  is_banned: boolean;
  retries: number;
}

export interface RateLimiterStatus {
  platform?: string;
  config: RateLimiterConfig;
  available_tokens: number;
  capacity: number;
  queue_size: number;
  is_paused: boolean;
  is_cooling: boolean;
  cooldown_left: number;
  last_ban_reason: string;
  total_requests: number;
  success_requests: number;
  banned_requests: number;
  retried_requests: number;
  recent_logs: RequestLogItem[];
}

export const apiGetLimiterStatus = (platform: "bili" | "zhihu" = "bili") =>
  fetchApi<RateLimiterStatus>(`/api/limiter/status?platform=${platform}`);

export const apiGetLimiterConfig = (platform: "bili" | "zhihu" = "bili") =>
  fetchApi<RateLimiterConfig>(`/api/limiter/config?platform=${platform}`);

export const apiUpdateLimiterConfig = (config: Partial<RateLimiterConfig> & { platform?: "bili" | "zhihu" }) =>
  fetchApi<{ success: boolean; platform: string; config: RateLimiterConfig }>("/api/limiter/config", {
    method: "POST",
    body: JSON.stringify(config)
  });

export const apiToggleLimiterPause = (paused?: boolean, platform: "bili" | "zhihu" = "bili") =>
  fetchApi<{ success: boolean; platform: string; is_paused: boolean }>("/api/limiter/pause", {
    method: "POST",
    body: JSON.stringify({ paused, platform })
  });

export const apiResetLimiterStats = (platform: "bili" | "zhihu" = "bili") =>
  fetchApi<{ success: boolean; platform: string }>("/api/limiter/reset_stats", {
    method: "POST",
    body: JSON.stringify({ platform })
  });

// -------------------------------- 多账号管理 (Accounts)
export interface AccountItem {
  mid: string;
  uname: string;
  face: string;
  is_current: number;
  is_enabled?: number;
  auto_refresh?: number;
  vip_type: number;
  created_at?: number;
  updated_at?: number;
}

export const apiGetAccounts = () =>
  fetchApi<{ accounts: AccountItem[] }>("/api/auth/accounts");

export const apiSwitchAccount = (mid: string) =>
  fetchApi<{ success: boolean; mid: string; uname: string; face: string; message: string }>("/api/auth/switch_account", {
    method: "POST",
    body: JSON.stringify({ mid })
  });

export const apiRemoveAccount = (mid: string) =>
  fetchApi<{ success: boolean; message: string }>("/api/auth/remove_account", {
    method: "POST",
    body: JSON.stringify({ mid })
  });

export const apiUpdateAccountFlags = (mid: string, flags: { is_enabled?: boolean; auto_refresh?: boolean }) =>
  fetchApi<{ success: boolean; accounts: AccountItem[] }>("/api/auth/update_account_flags", {
    method: "POST",
    body: JSON.stringify({ mid, ...flags })
  });

// -------------------------------- 单 URL 与合集解析 (Link Collection)
export interface ParsedVideoPage {
  cid: number;
  page: number;
  part: string;
  duration: string;
  duration_seconds: number;
}

export interface ParsedVideoResult {
  success: boolean;
  bvid: string;
  aid?: number;
  title: string;
  author: string;
  up_mid: number;
  face?: string;
  pic: string;
  desc?: string;
  pubdate?: string;
  duration?: string;
  url: string;
  parts: ParsedVideoPage[];
  season?: {
    id: number;
    title: string;
    cover: string;
    mid: number;
    ep_count: number;
    episodes: any[];
  } | null;
}

export const apiParseUrl = (url: string) =>
  fetchApi<ParsedVideoResult>("/api/parse/url", {
    method: "POST",
    body: JSON.stringify({ url })
  });

export const apiParseCollection = (req: {
  season_id?: number;
  series_id?: number;
  mid?: number;
  page?: number;
  page_size?: number;
}) =>
  fetchApi<{ success: boolean; total: number; items: any[] }>("/api/parse/collection", {
    method: "POST",
    body: JSON.stringify(req)
  });



// ======================================================================
// 知乎多平台扩展 API 与数据结构 (博主监听 / 收藏夹监听 / 我赞同的帖子 / 离线归档)
// ======================================================================

export interface ZhihuAuthInfo {
  is_login: boolean;
  url_token: string;
  uname: string;
  face: string;
  headline?: string;
  answer_count?: number;
  articles_count?: number;
  d_c0_ready?: boolean;
  verification_state?: "pending" | "verified" | "unavailable" | "expired" | "logged_out";
}

export interface ZhihuAccountItem {
  url_token: string;
  uname: string;
  face: string;
  headline?: string;
  is_current: number;
  is_enabled?: number;
  auto_refresh?: number;
  created_at?: number;
  updated_at?: number;
}

export interface ZhihuSummaryCounts {
  creator_unread: number;
  collection_unread: number;
  upvote_unread: number;
  total_unread: number;
  creator_total: number;
  collection_total: number;
  upvote_total: number;
  archived_total: number;
  creators_count?: number;
  collections_count?: number;
}

export interface ZhihuCreatorItem {
  url_token: string;
  user_id?: string;
  name: string;
  avatar_url: string;
  headline: string;
  answer_count: number;
  articles_count: number;
  follower_count: number;
  group_name: string;
  last_sync_time: number;
  latest_created: number;
  cached_count?: number;
  unread_count?: number;
}

export interface ZhihuCollectionItem {
  collection_id: string;
  title: string;
  description: string;
  creator_name: string;
  creator_token: string;
  item_count: number;
  follower_count: number;
  source_type: "my" | "following" | "custom" | string;
  last_sync_time: number;
  latest_created: number;
  cached_count?: number;
  unread_count?: number;
}

export interface ZhihuContentItem {
  content_id: string;
  source_type: "creator" | "collection" | "upvote" | "single" | string;
  source_id: string;
  content_type: "answer" | "article" | "zvideo" | string;
  question_id: string;
  title: string;
  excerpt: string;
  content_html?: string;
  content_url: string;
  cover_url: string;
  author_name: string;
  author_token: string;
  author_avatar: string;
  voteup_count: number;
  comment_count: number;
  created_time: number;
  updated_time: number;
  upvote_time: number;
  is_read: number;
  download_status: "none" | "downloading" | "success" | "failed" | string;
  local_file: string;
  is_paid?: number;
  paid_reason?: string;
  paid_check_version?: number;
  access_pending?: boolean;
  is_restricted?: number;
  restrict_reason?: string;
}

export const apiZhihuGetAuthStatus = () =>
  fetchApi<ZhihuAuthInfo>("/api/zhihu/auth/status");

export const apiZhihuGetAccounts = () =>
  fetchApi<{ accounts: ZhihuAccountItem[] }>("/api/zhihu/auth/accounts");

export const apiZhihuSwitchAccount = (url_token: string) =>
  fetchApi<{ success: boolean; url_token: string; uname: string; face: string; message: string }>(
    "/api/zhihu/auth/switch_account",
    {
      method: "POST",
      body: JSON.stringify({ url_token })
    }
  );

export const apiZhihuRemoveAccount = (url_token: string) =>
  fetchApi<{ success: boolean; message: string }>("/api/zhihu/auth/remove_account", {
    method: "POST",
    body: JSON.stringify({ url_token })
  });

export const apiZhihuUpdateAccountFlags = (url_token: string, flags: { is_enabled?: boolean; auto_refresh?: boolean }) =>
  fetchApi<{ success: boolean; accounts: ZhihuAccountItem[] }>("/api/zhihu/auth/update_account_flags", {
    method: "POST",
    body: JSON.stringify({ url_token, ...flags })
  });

export const apiZhihuGenerateQrCode = () =>
  fetchApi<{ success: boolean; qrcode_key?: string; qrcode_url?: string; qrcode_img?: string; message?: string }>(
    "/api/zhihu/auth/qrcode/generate",
    { method: "POST" }
  );

export const apiZhihuPollQrCode = (qrcode_key: string) =>
  fetchApi<{
    status: "waiting" | "scanned" | "success" | "expired" | "rotated" | "blocked" | "error";
    message: string;
    uname?: string;
    face?: string;
    url_token?: string;
    qrcode_key?: string;
    qrcode_url?: string;
    qrcode_img?: string;
    code?: number;
    redirect?: string;
  }>("/api/zhihu/auth/qrcode/poll", {
    method: "POST",
    body: JSON.stringify({ qrcode_key })
  });

export const apiZhihuStartBrowserLogin = () =>
  fetchApi<{ success: boolean; status: string; message: string }>(
    "/api/zhihu/auth/browser_login",
    { method: "POST" }
  );

export const apiZhihuPollBrowserLogin = () =>
  fetchApi<{
    status: "idle" | "running" | "success" | "failed" | string;
    message: string;
    uname?: string;
    face?: string;
    url_token?: string;
  }>("/api/zhihu/auth/browser_login/status");

export const apiGetSystemLogs = (lines: number = 150) =>
  fetchApi<{ log_file: string; lines: string[] }>(`/api/system/logs?lines=${lines}`);

export const apiZhihuImportCookie = (cookie: string) =>
  fetchApi<{ success: boolean; message: string; uname: string; face: string; url_token: string }>(
    "/api/zhihu/auth/import_cookie",
    {
      method: "POST",
      body: JSON.stringify({ cookie })
    }
  );

export const apiZhihuCancelLogin = () =>
  fetchApi<{ success: boolean }>("/api/zhihu/auth/cancel_login", { method: "POST" });

export const apiZhihuLogout = () =>
  fetchApi<{ success: boolean; message: string }>("/api/zhihu/auth/logout", {
    method: "POST"
  });

export const apiZhihuGetSummary = () =>
  fetchApi<ZhihuSummaryCounts>("/api/zhihu/summary");

export const apiZhihuRunAutoCheckNow = (autoArchive: boolean = false, scope: string = "all") =>
  fetchApi<{ success: boolean; new_count: number; message: string; summary: ZhihuSummaryCounts }>(
    "/api/zhihu/autocheck/run_now",
    {
      method: "POST",
      body: JSON.stringify({ auto_archive: autoArchive, scope })
    }
  );

export const apiZhihuGetCreators = (groupName: string = "all") =>
  fetchApi<{ total: number; items: ZhihuCreatorItem[] }>(
    `/api/zhihu/creators?group_name=${encodeURIComponent(groupName)}`
  );

export const apiZhihuSyncAccountCreators = (urlToken: string = "", maxPages: number = 3) =>
  fetchApi<{ success: boolean; count: number; items: ZhihuCreatorItem[]; message: string }>(
    "/api/zhihu/creators/sync_account",
    {
      method: "POST",
      body: JSON.stringify({ url_token: urlToken, max_pages: maxPages })
    }
  );

export const apiZhihuInitAccountData = () =>
  fetchApi<{
    success: boolean;
    uname: string;
    url_token: string;
    collections_count: number;
    upvotes_count: number;
    summary: ZhihuSummaryCounts;
    message: string;
  }>("/api/zhihu/init_account_data", {
    method: "POST"
  });

export const apiZhihuAddCreator = (inputText: string, groupName: string = "默认分组") =>
  fetchApi<{ success: boolean; creator: ZhihuCreatorItem; fetched_count: number; message: string }>(
    "/api/zhihu/creators/add",
    {
      method: "POST",
      body: JSON.stringify({ input_text: inputText, group_name: groupName })
    }
  );

export const apiZhihuRemoveCreator = (urlToken: string) =>
  fetchApi<{ success: boolean; url_token: string }>("/api/zhihu/creators/remove", {
    method: "POST",
    body: JSON.stringify({ url_token: urlToken })
  });

export const apiZhihuSyncCreator = (urlToken: string, deepScrape: boolean = false, maxPages: number = 1) =>
  fetchApi<{ success: boolean; url_token: string; fetched_count: number; new_count: number; message: string }>(
    "/api/zhihu/creators/sync",
    {
      method: "POST",
      body: JSON.stringify({ url_token: urlToken, deep_scrape: deepScrape, max_pages: maxPages })
    }
  );

export const apiZhihuGetCollections = (sourceType: string = "all") =>
  fetchApi<{ total: number; items: ZhihuCollectionItem[] }>(
    `/api/zhihu/collections?source_type=${encodeURIComponent(sourceType)}`
  );

export const apiZhihuSyncAccountCollections = (urlToken: string = "") =>
  fetchApi<{ success: boolean; count: number; items: ZhihuCollectionItem[]; message: string }>(
    "/api/zhihu/collections/sync_account",
    {
      method: "POST",
      body: JSON.stringify({ url_token: urlToken })
    }
  );

export const apiZhihuAddCollection = (inputText: string) =>
  fetchApi<{ success: boolean; collection: ZhihuCollectionItem; fetched_count: number; message: string }>(
    "/api/zhihu/collections/add",
    {
      method: "POST",
      body: JSON.stringify({ input_text: inputText })
    }
  );

export const apiZhihuRemoveCollection = (collectionId: string) =>
  fetchApi<{ success: boolean; collection_id: string }>("/api/zhihu/collections/remove", {
    method: "POST",
    body: JSON.stringify({ collection_id: collectionId })
  });

export const apiZhihuSyncCollectionItems = (collectionId: string, deepScrape: boolean = false, maxPages: number = 1) =>
  fetchApi<{ success: boolean; collection_id: string; fetched_count: number; new_count: number; message: string }>(
    "/api/zhihu/collections/sync_items",
    {
      method: "POST",
      body: JSON.stringify({ collection_id: collectionId, deep_scrape: deepScrape, max_pages: maxPages })
    }
  );

export const apiZhihuGetUpvotesStatus = () =>
  fetchApi<{ url_token: string; latest_upvote_time: number; total_cached: number; unread_count: number }>(
    "/api/zhihu/upvotes/status"
  );

export const apiZhihuSyncUpvotes = (urlToken: string = "", deepScrape: boolean = false, maxPages: number = 1) =>
  fetchApi<{ success: boolean; url_token: string; fetched_count: number; new_count: number; message: string }>(
    "/api/zhihu/upvotes/sync",
    {
      method: "POST",
      body: JSON.stringify({ url_token: urlToken, deep_scrape: deepScrape, max_pages: maxPages })
    }
  );

export const apiZhihuGetContents = (params: {
  source_type?: string;
  source_id?: string;
  content_type?: string;
  only_unread?: boolean;
  only_archived?: boolean;
  search?: string;
}) => {
  const qs = new URLSearchParams({
    source_type: params.source_type || "all",
    source_id: params.source_id || "all",
    content_type: params.content_type || "all",
    only_unread: String(Boolean(params.only_unread)),
    only_archived: String(Boolean(params.only_archived)),
    search: params.search || ""
  });
  return fetchApi<{ total: number; items: ZhihuContentItem[]; summary: ZhihuSummaryCounts }>(
    `/api/zhihu/contents?${qs.toString()}`
  );
};

export const apiZhihuGetContentDetail = (contentId: string) =>
  fetchApi<{ success: boolean; item: ZhihuContentItem }>(
    `/api/zhihu/content/detail?content_id=${encodeURIComponent(contentId)}`
  );

export const apiZhihuMarkRead = (payload: { content_id?: string; source_type?: string; source_id?: string }) =>
  fetchApi<{ success: boolean; affected: number; summary: ZhihuSummaryCounts }>(
    "/api/zhihu/contents/mark_read",
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );

export const apiZhihuParseUrl = (url: string) =>
  fetchApi<{ success: boolean; count: number; items: ZhihuContentItem[]; message: string }>(
    "/api/zhihu/parse_url",
    {
      method: "POST",
      body: JSON.stringify({ url })
    }
  );

export const apiZhihuArchiveItems = (contentIds: string[]) =>
  fetchApi<{ success: boolean; count: number; message: string }>("/api/zhihu/archive", {
    method: "POST",
    body: JSON.stringify({ content_ids: contentIds })
  });
