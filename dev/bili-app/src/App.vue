<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import AppIcon from "./components/AppIcon.vue";
import {
  serverOnline,
  initServerConnection,
  apiGetAuthStatus,
  apiGenerateQrCode,
  apiPollQrCode,
  apiImportCookie,
  apiLogout,
  apiGetTags,
  apiGetFollowings,
  apiGetVideosIndex,
  apiDownloadSingle,
  apiBatchDownload,
  apiGetDownloadTasks,
  apiRetryDownload,
  apiCancelDownload,
  apiClearCompletedDownloads,
  apiReconcileDownloads,
  apiGetSettings,
  apiCheckUpdate,
  apiStartUpdateDownload,
  apiGetUpdateDownloadStatus,
  apiLaunchUpdateInstaller,
  type UpdateCheckResult,
  type UpdateDownloadStatus,
  apiUpdateSettings,
  apiGetLimiterStatus,
  apiUpdateLimiterConfig,
  apiToggleLimiterPause,
  apiResetLimiterStats,
  apiGetFavFolders,
  apiGetFavVideos,
  apiGetAccounts,
  apiSwitchAccount,
  apiRemoveAccount,
  apiGetUpCollections,
  apiToggleCollectionMonitor,
  apiToggleUpMonitor,
  apiParseCollection,
  type BiliCollection,
  apiUpdateAccountFlags,
  apiOpenFolder,
  apiSelectFolder,
  apiOpenFile,
  apiParseUrl,
  apiSubscribeLocalUp,
  apiGetAutoCheckStatus,
  apiRunAutoCheckNow,
  apiMarkVideosRead,
  formatPicUrl,
  getProxyPicUrl,
  type UserInfo,
  type RelationTag,
  type VideoItem,
  type DownloadTaskItem,
  type AppSettings,
  type RateLimiterConfig,
  type RateLimiterStatus,
  type FavFolder,
  type AccountItem,
  type ParsedVideoResult,
  type AutoCheckStatus,
  apiZhihuGetAuthStatus,
  apiZhihuGetAccounts,
  apiZhihuSwitchAccount,
  apiZhihuRemoveAccount,
  apiZhihuUpdateAccountFlags,
  apiZhihuGenerateQrCode,
  apiZhihuPollQrCode,
  apiZhihuStartBrowserLogin,
  apiZhihuPollBrowserLogin,
  apiZhihuImportCookie,
  apiZhihuLogout,
  apiZhihuCancelLogin,
  apiGetSystemLogs,
  apiZhihuGetSummary,
  apiZhihuRunAutoCheckNow,
  apiZhihuGetCreators,
  apiZhihuSyncAccountCreators,
  apiZhihuInitAccountData,
  apiZhihuAddCreator,
  apiZhihuRemoveCreator,
  apiZhihuSyncCreator,
  apiZhihuGetCollections,
  apiZhihuSyncAccountCollections,
  apiZhihuAddCollection,
  apiZhihuRemoveCollection,
  apiZhihuSyncCollectionItems,
  apiZhihuGetUpvotesStatus,
  apiZhihuSyncUpvotes,
  apiZhihuGetContents,
  apiZhihuGetContentDetail,
  apiZhihuMarkRead,
  apiZhihuParseUrl,
  apiZhihuArchiveItems,
  type ZhihuAuthInfo,
  type ZhihuAccountItem,
  type ZhihuSummaryCounts,
  type ZhihuCreatorItem,
  type ZhihuCollectionItem,
  type ZhihuContentItem
} from "./api";

// 状态定义
const user = ref<UserInfo>({ is_login: false });
const accounts = ref<AccountItem[]>([]);
const zhihuAccounts = ref<ZhihuAccountItem[]>([]);
const showAccountPopover = ref<boolean>(false);
const showZhihuAccountPopover = ref<boolean>(false);
const accountCenterFilter = ref<"all" | "zhihu" | "bili">("all");
const taskAccountFilter = ref<string>("all");
const savedTheme = (typeof localStorage !== "undefined")
  ? (localStorage.getItem("gatherbox_theme") || localStorage.getItem("bilifollow_theme"))
  : null;
const isWindowMaximized = ref<boolean>(false);
const themeMode = ref<"dark" | "light">((savedTheme === "dark" || savedTheme === "light") ? savedTheme : "light");

const enabledBiliAccounts = computed(() => accounts.value.filter((a) => a.is_enabled !== 0));
const enabledZhihuAccounts = computed(() => zhihuAccounts.value.filter((a) => a.is_enabled !== 0));

const tags = ref<RelationTag[]>([]);
const currentTagId = ref<number>(0);
const isAll = ref<boolean>(true); // 默认全量关注，一览所有关注UP
const selectedUpMid = ref<number | null>(null);
const showDisclaimerModal = ref<boolean>(false);

interface AppToastItem {
  id: number;
  message: string;
  type: "success" | "error";
}
const appToasts = ref<AppToastItem[]>([]);
let toastIdSeq = 1;

function showToast(msg: string, explicitType?: "success" | "error") {
  const text = String(msg || "").trim();
  if (!text) return;
  const isErr =
    explicitType === "error" ||
    (!explicitType && /(失败|错误|异常|受限|禁止|未获取|请至少|请先|无效|已过期)/.test(text));
  const tType: "success" | "error" = isErr ? "error" : "success";
  const id = toastIdSeq++;
  appToasts.value.push({ id, message: text, type: tType });
  if (appToasts.value.length > 4) {
    appToasts.value.shift();
  }
  setTimeout(() => {
    appToasts.value = appToasts.value.filter((t) => t.id !== id);
  }, tType === "error" ? 3800 : 2600);
}
const upCollections = ref<BiliCollection[]>([]);
const loadingUpCollections = ref<boolean>(false);
const showUpCollectionsPanel = ref<boolean>(false);
const currentUpMonitored = ref<boolean>(true);
const selectedUpAuthor = computed(() => {
  if (!selectedUpMid.value) return null;
  return bloggers.value.find((b) => Number(b.mid) === Number(selectedUpMid.value)) || null;
});
const bloggers = ref<any[]>([]);
const upSearchKeyword = ref<string>(""); // 二级侧边栏 UP主 实时检索关键词

const filteredBloggers = computed(() => {
  const kw = upSearchKeyword.value.trim().toLowerCase();
  if (!kw) return bloggers.value;
  return bloggers.value.filter(
    (up) =>
      String(up.uname || "").toLowerCase().includes(kw) ||
      String(up.mid || "").includes(kw)
  );
});

// 模式与收藏夹状态
const activeMode = ref<"follow" | "fav" | "link">("follow");
const favFolders = ref<FavFolder[]>([]);
const currentFavId = ref<number | null>(null);

// 链接采集与合集解析状态
const linkInput = ref<string>("");
const parsingLink = ref<boolean>(false);
const parseError = ref<string>("");
const parsedResult = ref<ParsedVideoResult | null>(null);
const selectedPartCids = ref<number[]>([]);
const selectedEpisodeBvids = ref<string[]>([]);

// 官方 Electron 登录状态
const isElectron = computed(() => typeof window !== "undefined" && !!(window as any).electronAPI);
const showElectronLoginModal = ref<boolean>(false);
const electronLoginStatus = ref<string>("");

const videos = ref<VideoItem[]>([]);
const totalVideos = ref<number>(0);
const loadingVideos = ref<boolean>(false);
const searchKeyword = ref<string>("");

// 下载配置与任务队列
const audioOnly = ref<boolean>(true); // 默认仅下载音频（知识库推荐）
const selectedQuality = ref<string>("1080P");
const selectedAudioQuality = ref<string>("30280");
const settingBlockPaidVideos = ref<boolean>(true);
const allTasks = ref<DownloadTaskItem[]>([]);
const activeDownloadCount = ref<number>(0);
let taskPollTimer: any = null;

// 全局任务抽屉 Drawer 与统一设置中心
const showTaskDrawer = ref<boolean>(false);
const showSettingsModal = ref<boolean>(false);
const activeSettingsTab = ref<"download" | "autocheck" | "limiter" | "appearance" | "about">("download");
const drawerTab = ref<"all" | "running" | "success" | "failed">("all");
const appSettings = ref<AppSettings>({
  download_dir: "",
  download_max_workers: 3,
  max_workers: 3,
  refresh_on_select: false,
  auto_check_enabled: false,
  auto_check_interval: 30,
  auto_check_scope: "special",
  auto_download_new: false,
  theme: "dark",
  database_file: ""
});
const settingDownloadDir = ref<string>("");
const settingWorkers = ref<number>(3);
const settingRefreshOnSelect = ref<boolean>(localStorage.getItem("gatherbox_refresh_on_select") === "true");
const settingAutoCheck = ref<boolean>(false);
const settingCheckInterval = ref<number>(30);
const settingCheckScope = ref<"special" | "all" | "all_fav">("special");
const settingAutoDownload = ref<boolean>(false);
const isReconciling = ref<boolean>(false);
const reconcileNotice = ref<string>("");

// 本地免关注订阅与深度抓取状态
const localSubInput = ref<string>("");
const subscribingUp = ref<boolean>(false);
const deepScraping = ref<boolean>(false);

// 追更巡检实时状态
const autoCheckStatus = ref<AutoCheckStatus>({
  is_running: false,
  last_check_time: 0,
  last_new_count: 0,
  total_new_found: 0,
  last_message: "尚未执行巡检"
});
const runningManualCheck = ref<boolean>(false);

// 接口限流与令牌桶调度状态
const limiterStatus = ref<RateLimiterStatus>({
  config: {
    rate: 0.8,
    capacity: 3.0,
    min_interval: 1.0,
    jitter: 0.4,
    ban_cooldown: 15.0,
    max_retries: 3,
    is_paused: false
  },
  available_tokens: 3.0,
  capacity: 3.0,
  queue_size: 0,
  is_paused: false,
  is_cooling: false,
  cooldown_left: 0,
  last_ban_reason: "",
  total_requests: 0,
  success_requests: 0,
  banned_requests: 0,
  retried_requests: 0,
  recent_logs: []
});
const limiterForm = ref<RateLimiterConfig>({ ...limiterStatus.value.config });
let limiterPollTimer: any = null;

// 计算单个视频卡片的下载状态与本地文件
function getVideoDownloadState(v: VideoItem) {
  const matchingTasks = allTasks.value.filter((t) => t.bvid === v.bvid);
  const task =
    matchingTasks.find((t) => t.audio_only === audioOnly.value) ||
    matchingTasks.find((t) => t.status === "downloading" || t.status === "pending") ||
    matchingTasks[0];
  if (task) {
    if (task.status === "success" && task.audio_only !== audioOnly.value) {
      return null;
    }
    return {
      task_key: task.task_key || task.bvid,
      status: task.status,
      file_path: task.file_path,
      progress: task.progress,
      speed: task.speed,
      error: task.error,
      is_stopping: task.is_stopping,
      can_retry: task.can_retry
    };
  }
  if (v.download_status && v.download_status !== "none") {
    if (v.download_status === "interrupted") {
      return {
        task_key: v.bvid,
        status: "failed",
        file_path: v.local_file,
        progress: 0,
        speed: "已中断，可重新下载",
        error: "已中断，可重新下载",
        is_stopping: false,
        can_retry: true
      };
    }
    if (v.download_status === "success" && v.local_file) {
      const lower = v.local_file.toLowerCase();
      const isAudioFile = lower.endsWith(".m4a") || lower.endsWith(".mp3") || lower.endsWith(".aac");
      if (isAudioFile !== audioOnly.value) {
        return null;
      }
    }
    return {
      task_key: v.bvid,
      status: v.download_status,
      file_path: v.local_file,
      progress: v.download_status === "success" ? 100 : 0,
      speed: v.restrict_reason || "",
      error: null,
      is_stopping: false,
      can_retry: true
    };
  }
  return null;
}

function isVideoRestricted(v: VideoItem): boolean {
  if (!settingBlockPaidVideos.value) return false;
  if (v.is_restricted && v.is_restricted > 0) return true;
  const st = getVideoDownloadState(v);
  return st?.status === "restricted";
}

function getVideoRestrictReason(v: VideoItem): string {
  if (v.restrict_reason) return v.restrict_reason;
  const st = getVideoDownloadState(v);
  if (st?.status === "restricted") return st.speed || "付费/受限";
  return "付费/受限";
}

// 抽屉任务统计与过滤
const runningCount = computed(() => allTasks.value.filter((t) => t.status === "downloading" || t.status === "pending").length);
const downloadingCount = computed(() => allTasks.value.filter((t) => t.status === "downloading").length);
const pendingCount = computed(() => allTasks.value.filter((t) => t.status === "pending").length);
const successCount = computed(() => allTasks.value.filter((t) => t.status === "success").length);
const failedCount = computed(() => allTasks.value.filter((t) => t.status === "failed").length);

// 弹出菜单状态
const showZhihuMoreMenu = ref<boolean>(false);
const showZhihuMetaBox = ref<boolean>(false);
const showBiliDownloadPopover = ref<boolean>(false);
const showBiliMorePopover = ref<boolean>(false);
const showTaskMoreMenu = ref<boolean>(false);

const currentBiliContextTitle = computed(() => {
  if (activeMode.value === "fav") {
    const f = favFolders.value.find((item) => item.id === currentFavId.value);
    return f ? f.title : "我的收藏夹";
  }
  if (selectedUpMid.value) {
    return selectedUpAuthor.value?.uname || `UP主 (UID: ${selectedUpMid.value})`;
  }
  if (isAll.value) return "全部关注";
  if (currentTagId.value === -10) return "特别关注";
  if (currentTagId.value === 0) return "默认分组";
  const tag = tags.value.find((t) => t.tagid === currentTagId.value);
  return tag ? tag.name : "关注分组";
});

const taskAccountList = computed(() => {
  const set = new Set<string>();
  for (const z of zhihuAccounts.value) {
    if (z.uname) set.add(`zhihu:${z.uname}`);
  }
  for (const b of accounts.value) {
    if (b.uname) set.add(`bili:${b.uname}`);
  }
  for (const t of allTasks.value) {
    const plat = t.platform || (t.bvid.startsWith("zhihu_") ? "zhihu" : "bili");
    const acc = t.account_name || (plat === "zhihu" ? zhihuUser.value.uname || "知乎账号" : user.value.uname || "B站账号");
    set.add(`${plat}:${acc}`);
  }
  return Array.from(set).map((key) => {
    const [plat, ...rest] = key.split(":");
    const name = rest.join(":");
    return {
      key,
      platform: plat as "zhihu" | "bili",
      name,
      count: allTasks.value.filter((t) => {
        const tp = t.platform || (t.bvid.startsWith("zhihu_") ? "zhihu" : "bili");
        const ta = t.account_name || (tp === "zhihu" ? zhihuUser.value.uname || "知乎账号" : user.value.uname || "B站账号");
        return `${tp}:${ta}` === key;
      }).length
    };
  });
});

const accountScopedTasks = computed(() => {
  let list = allTasks.value;
  if (taskAccountFilter.value !== "all") {
    list = list.filter((t) => {
      const tp = t.platform || (t.bvid.startsWith("zhihu_") ? "zhihu" : "bili");
      const ta = t.account_name || (tp === "zhihu" ? zhihuUser.value.uname || "知乎账号" : user.value.uname || "B站账号");
      return `${tp}:${ta}` === taskAccountFilter.value;
    });
  }
  const kw = activePlatform.value === "tasks" ? searchKeyword.value.trim().toLowerCase() : "";
  if (kw) {
    list = list.filter(
      (t) =>
        (t.title || "").toLowerCase().includes(kw) ||
        (t.author || "").toLowerCase().includes(kw) ||
        (t.bvid || "").toLowerCase().includes(kw)
    );
  }
  return list;
});

const scopedFailedTaskIds = computed(() =>
  accountScopedTasks.value
    .filter((t) => t.status === "failed" && t.can_retry !== false && !t.is_stopping)
    .map((t) => t.task_key || t.bvid)
);
const scopedCancelledTaskIds = computed(() =>
  accountScopedTasks.value
    .filter((t) => t.status === "cancelled" && t.can_retry !== false && !t.is_stopping)
    .map((t) => t.task_key || t.bvid)
);
const scopedCompletedTaskIds = computed(() =>
  accountScopedTasks.value.filter((t) => t.status === "success").map((t) => t.task_key || t.bvid)
);

const filteredDrawerTasks = computed(() => {
  let list = accountScopedTasks.value;
  if (drawerTab.value === "running") {
    list = list.filter((t) => t.status === "downloading" || t.status === "pending");
  } else if (drawerTab.value === "success") {
    list = list.filter((t) => t.status === "success");
  } else if (drawerTab.value === "failed") {
    list = list.filter((t) => t.status === "failed" || t.status === "cancelled");
  }
  return list;
});

// 弹窗状态
const showQrModal = ref<boolean>(false);
const biliLoginTab = ref<"qrcode" | "cookie">("qrcode");
const qrImg = ref<string>("");
const qrKey = ref<string>("");
const qrStatusText = ref<string>("正在加载二维码...");
let qrPollTimer: any = null;

const showCookieModal = ref<boolean>(false);
const cookieInput = ref<string>("");
const cookieError = ref<string>("");
const cookieLoading = ref<boolean>(false);

// 过滤后的视频列表
const filteredVideos = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase();
  if (!kw) return videos.value;
  return videos.value.filter(
    (v) =>
      v.视频名字.toLowerCase().includes(kw) ||
      v.UP名字.toLowerCase().includes(kw) ||
      v.bvid.toLowerCase().includes(kw)
  );
});

// 计算当前列表中尚未下载的视频（自动过滤已下载与受限付费/充电视频）
const undownloadedVideos = computed(() =>
  filteredVideos.value.filter((v) => {
    if (isVideoRestricted(v)) return false;
    const st = getVideoDownloadState(v);
    return !st || (st.status !== "success" && st.status !== "restricted");
  })
);

// 计算当前列表中未读的新追更视频数量
const unreadVideosCount = computed(() =>
  filteredVideos.value.filter((v) => v.is_read === 0).length
);

// 初始化
onMounted(async () => {
  // 立即恢复保存的界面缩放偏好并触发响应式重算
  applyUiScale();

  // 检查首次启动免责声明（优先读取本地缓存，待连接后端后与 SQLite 持久化状态双向同步）
  const localDisclaimerAccepted = localStorage.getItem("gatherbox_disclaimer_accepted") === "true";

  console.log("%c BiliFollow 前端界面初始化就绪", "color: #3b82f6; font-size: 15px; font-weight: bold;");

  // 恢复保存的主题
  const savedTheme = localStorage.getItem("bilifollow_theme");
  if (savedTheme === "light" || savedTheme === "dark") {
    themeMode.value = savedTheme;
  }

  // 监听 Electron 登录广播
  if (isElectron.value) {
    (window as any).electronAPI.onLoginStatusChange(async (res: any) => {
      if (res && res.success) {
        showElectronLoginModal.value = false;
        await checkAuth();
        await loadAccounts();
        await refreshCurrentView(true);
      }
    });
  }

  // 连接本地后端 (支持动态分配端口与安全 Token 握手，轮询等待后端就绪)
  let connected = await initServerConnection();
  if (!connected) {
    for (let i = 0; i < 25; i++) {
      await new Promise((r) => setTimeout(r, 400));
      connected = await initServerConnection();
      if (connected) break;
    }
  }

  if (connected) {
    console.log("%c[BiliFollow] 本地 API 服务连接成功！", "color: #10b981; font-weight: bold;");
    try {
      const cfg = await apiGetSettings();
      appSettings.value = cfg;
      settingDownloadDir.value = cfg.download_dir;
      settingWorkers.value = cfg.download_max_workers || 3;
      settingRefreshOnSelect.value = !!cfg.refresh_on_select;
      localStorage.setItem("gatherbox_refresh_on_select", String(settingRefreshOnSelect.value));
      settingAutoCheck.value = !!cfg.auto_check_enabled;
      settingCheckInterval.value = cfg.auto_check_interval || 30;
      settingCheckScope.value = cfg.auto_check_scope || "special";
      settingAutoDownload.value = !!cfg.auto_download_new;
      if (cfg.default_video_quality) selectedQuality.value = cfg.default_video_quality;
      if (cfg.default_audio_quality) selectedAudioQuality.value = cfg.default_audio_quality;
      if (cfg.block_paid_videos !== undefined) settingBlockPaidVideos.value = !!cfg.block_paid_videos;
      if (cfg.disclaimer_accepted || localDisclaimerAccepted) {
        localStorage.setItem("gatherbox_disclaimer_accepted", "true");
        showDisclaimerModal.value = false;
        if (!cfg.disclaimer_accepted && localDisclaimerAccepted) {
          apiUpdateSettings({ disclaimer_accepted: true }).catch(() => {});
        }
      } else {
        showDisclaimerModal.value = true;
      }
      captureSettingsSnapshot();
    } catch (_) {
      if (!localDisclaimerAccepted) {
        showDisclaimerModal.value = true;
      }
    }
    await checkAuth(false);
    await loadAccounts();
    await initZhihuWorkspace(false);
    checkAppUpdate(false, true);
  } else if (!localDisclaimerAccepted) {
    showDisclaimerModal.value = true;
  }

  startTaskPolling();
  startLimiterPolling();

  handleWindowResize();
  window.addEventListener("resize", handleWindowResize);
  window.addEventListener("keydown", handleGlobalKeydown);
  window.addEventListener("contextmenu", (e) => e.preventDefault());
});

function handleWindowResize() {
  const scale = uiScale.value || 1;
  const effectiveWidth = window.innerWidth / scale;
  if (effectiveWidth < 1180) {
    primarySidebarCollapsed.value = true;
  }
  if (effectiveWidth < 1024) {
    showRightPanel.value = false;
  }
}

let lastFocusedElement: HTMLElement | null = null;

function rememberAndFocusModal(modalSelector: string) {
  if (document.activeElement instanceof HTMLElement && !document.activeElement.closest('[role="dialog"]')) {
    lastFocusedElement = document.activeElement;
  }
  nextTick(() => {
    const dialogEl = document.querySelector<HTMLElement>(modalSelector);
    if (dialogEl) {
      const focusable = dialogEl.querySelector<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      (focusable || dialogEl).focus();
    }
  });
}

function restoreLastFocus() {
  if (lastFocusedElement && typeof lastFocusedElement.focus === "function") {
    const target = lastFocusedElement;
    lastFocusedElement = null;
    nextTick(() => target.focus());
  }
}

function handleGlobalKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") {
    if (showZhihuMoreMenu.value || showBiliDownloadPopover.value || showBiliMorePopover.value || showTaskMoreMenu.value || showAccountPopover.value || showZhihuAccountPopover.value) {
      showZhihuMoreMenu.value = false;
      showBiliDownloadPopover.value = false;
      showBiliMorePopover.value = false;
      showTaskMoreMenu.value = false;
      showAccountPopover.value = false;
      showZhihuAccountPopover.value = false;
      e.preventDefault();
      return;
    }
    if (showUpdateModal.value && !showDisclaimerModal.value) {
      showUpdateModal.value = false;
      restoreLastFocus();
      e.preventDefault();
      return;
    }
    if (showTaskDrawer.value) {
      showTaskDrawer.value = false;
      restoreLastFocus();
      e.preventDefault();
      return;
    }
    if (showQrModal.value || showCookieModal.value) {
      closeQrModal();
      showCookieModal.value = false;
      restoreLastFocus();
      e.preventDefault();
      return;
    }
    if (showElectronLoginModal.value) {
      handleCloseElectronLogin();
      restoreLastFocus();
      e.preventDefault();
      return;
    }
    if (showZhihuLoginModal.value) {
      closeZhihuLoginModal();
      restoreLastFocus();
      e.preventDefault();
      return;
    }
    if (activePlatform.value === "settings") {
      closeSettings();
      e.preventDefault();
      return;
    }
  } else if ((e.key === "Enter" || e.key === " ") && document.activeElement instanceof HTMLElement) {
    const el = document.activeElement;
    if (el.getAttribute("role") === "button" && el.tagName !== "BUTTON" && el.tagName !== "INPUT") {
      e.preventDefault();
      el.click();
    }
  } else if (e.key === "Tab") {
    const activeDialog = document.querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]');
    if (activeDialog) {
      const focusables = Array.from(
        activeDialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
        )
      ).filter((el) => el.offsetParent !== null);
      if (focusables.length > 0) {
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          last.focus();
          e.preventDefault();
        } else if (!e.shiftKey && document.activeElement === last) {
          first.focus();
          e.preventDefault();
        }
      }
    }
  }
}

onUnmounted(() => {
  if (taskPollTimer) clearInterval(taskPollTimer);
  if (qrPollTimer) clearInterval(qrPollTimer);
  if (limiterPollTimer) clearInterval(limiterPollTimer);
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
  stopUpdateDownloadPolling();
  stopZhihuTimers();
  if (zhihuAuthRetryTimer) clearTimeout(zhihuAuthRetryTimer);
  ++zhihuLoginGeneration;
  if (showZhihuLoginModal.value) void apiZhihuCancelLogin().catch(() => {});
  window.removeEventListener("resize", handleWindowResize);
  window.removeEventListener("keydown", handleGlobalKeydown);
});

// 主题切换 (深色 / 浅色)
function setTheme(mode: "dark" | "light") {
  themeMode.value = mode;
  localStorage.setItem("gatherbox_theme", mode);
  localStorage.setItem("bilifollow_theme", mode);
  apiUpdateSettings({ theme: mode }).catch(() => {});
}

function toggleTheme() {
  setTheme(themeMode.value === "dark" ? "light" : "dark");
}

// 加载已保存的多账号列表
async function loadAccounts() {
  try {
    const res = await apiGetAccounts();
    accounts.value = res.accounts || [];
  } catch (err) {
    console.warn("加载多账号失败:", err);
  }
}

async function loadZhihuAccounts() {
  try {
    const res = await apiZhihuGetAccounts();
    zhihuAccounts.value = res.accounts || [];
  } catch (err) {
    console.warn("加载知乎多账号失败:", err);
  }
}

// 切换 B站 账号
async function switchAccount(mid: string) {
  try {
    await apiSwitchAccount(mid);
    showAccountPopover.value = false;
    await checkAuth(settingRefreshOnSelect.value);
    await loadAccounts();
  } catch (err: any) {
    showToast("切换账号失败: " + err.message);
  }
}

// 移除 B站 账号
async function removeAccount(mid: string, uname: string) {
  if (confirm(`确定要从本地移除 B站账号【${uname}】吗？`)) {
    try {
      await apiRemoveAccount(mid);
      await loadAccounts();
      await checkAuth();
    } catch (err: any) {
      showToast("移除账号失败: " + err.message);
    }
  }
}

async function toggleBiliAccountFlag(acc: AccountItem, field: "is_enabled" | "auto_refresh") {
  const nextVal = acc[field] === 0;
  try {
    const res = await apiUpdateAccountFlags(acc.mid, { [field]: nextVal });
    accounts.value = res.accounts || [];
  } catch (err: any) {
    showToast("更新账号状态失败: " + err.message);
  }
}

// 切换知乎账号
async function switchZhihuAccount(urlToken: string) {
  try {
    await apiZhihuSwitchAccount(urlToken);
    showZhihuAccountPopover.value = false;
    await initZhihuWorkspace(settingRefreshOnSelect.value);
    await loadZhihuAccounts();
  } catch (err: any) {
    showToast("切换知乎账号失败: " + err.message);
  }
}

// 移除知乎账号
async function removeZhihuAccount(urlToken: string, uname: string) {
  if (confirm(`确定要从本地移除知乎账号【${uname}】吗？`)) {
    try {
      await apiZhihuRemoveAccount(urlToken);
      await loadZhihuAccounts();
      await initZhihuWorkspace();
    } catch (err: any) {
      showToast("移除知乎账号失败: " + err.message);
    }
  }
}

async function toggleZhihuAccountFlag(acc: ZhihuAccountItem, field: "is_enabled" | "auto_refresh") {
  const nextVal = acc[field] === 0;
  try {
    const res = await apiZhihuUpdateAccountFlags(acc.url_token, { [field]: nextVal });
    zhihuAccounts.value = res.accounts || [];
    await initZhihuWorkspace(false);
  } catch (err: any) {
    showToast("更新知乎账号状态失败: " + err.message);
  }
}

function toggleAccountPopover() {
  loadAccounts();
  showAccountPopover.value = !showAccountPopover.value;
}

function toggleZhihuAccountPopover() {
  loadZhihuAccounts();
  showZhihuAccountPopover.value = !showZhihuAccountPopover.value;
}

function switchToAccountCenter(filter: "all" | "zhihu" | "bili" = "all") {
  if (!confirmLeaveSettings()) return;
  activePlatform.value = "accounts";
  accountCenterFilter.value = filter;
  loadAccounts();
  loadZhihuAccounts();
}

// 检查登录态
async function checkAuth(autoRefreshNetwork: boolean = false) {
  try {
    const res = await apiGetAuthStatus();
    user.value = res;
    console.log(`%c[BiliFollow] 登录态检查结果: ${res.is_login ? `已登录 (${res.uname}, MID: ${res.mid})` : '未登录或Cookie失效'}`, res.is_login ? "color: #10b981;" : "color: #f59e0b;");
    if (res.is_login) {
      await loadTags();
      await loadFavFolders(autoRefreshNetwork);
      await loadVideos(autoRefreshNetwork, !autoRefreshNetwork);
    }
  } catch (err) {
    console.warn("[BiliFollow] 获取登录信息失败:", err);
  }
}

// 加载关注分组
async function loadTags() {
  try {
    const res = await apiGetTags();
    tags.value = res.tags || [];
    console.log(`[BiliFollow] 成功载入 ${tags.value.length} 个关注分组:`, tags.value.map(t => t.name).join(", "));
  } catch (err) {
    console.error("加载分组失败:", err);
  }
}

// 切换分组
async function switchGroup(tagid: number, all: boolean = false) {
  console.log(`[BiliFollow] 切换分组 -> TagID: ${tagid}, 全量: ${all}`);
  currentTagId.value = tagid;
  isAll.value = all;
  selectedUpMid.value = null; // 切换分组重置单博主过滤
  const shouldRefresh = settingRefreshOnSelect.value;
  await loadVideos(shouldRefresh, !shouldRefresh);
}

// 免责声明确认与退出
function handleAcceptDisclaimer() {
  localStorage.setItem("gatherbox_disclaimer_accepted", "true");
  showDisclaimerModal.value = false;
  apiUpdateSettings({ disclaimer_accepted: true }).catch(() => {});
}

async function handleRejectDisclaimer() {
  await handleCloseWindow();
}

// 加载 UP 主的合集与系列
async function loadUpCollections(mid: number, forceRefresh: boolean = false) {
  loadingUpCollections.value = true;
  try {
    const res = await apiGetUpCollections(mid, forceRefresh);
    if (res && res.success) {
      upCollections.value = res.collections || [];
      currentUpMonitored.value = res.up_monitored !== false;
    }
  } catch (e) {
    console.error("加载UP主合集列表失败:", e);
  } finally {
    loadingUpCollections.value = false;
  }
}

// 切换 UP 主日常投稿监听开关 A
async function handleToggleUpMonitor(mid: number, isMonitored: boolean) {
  try {
    const res = await apiToggleUpMonitor(mid, isMonitored);
    if (res && res.success) {
      currentUpMonitored.value = res.is_monitored;
      const target = bloggers.value.find((b) => Number(b.mid) === Number(mid));
      if (target) {
        target.is_monitored = res.is_monitored ? 1 : 0;
      }
    }
  } catch (e: any) {
    showToast("更新UP主监听状态失败: " + (e.message || e));
  }
}

// 切换特定合集监听开关 B
async function handleToggleCollectionMonitor(col: BiliCollection) {
  try {
    const nextState = !col.is_monitored;
    const res = await apiToggleCollectionMonitor(
      Number(col.mid),
      col.collection_type,
      Number(col.collection_id),
      nextState
    );
    if (res && res.success) {
      col.is_monitored = res.is_monitored ? 1 : 0;
    }
  } catch (e: any) {
    showToast("更新合集监听状态失败: " + (e.message || e));
  }
}

// 一键下载指定合集全部视频
async function handleDownloadCollection(col: BiliCollection) {
  try {
    loadingUpCollections.value = true;
    const res = await apiParseCollection({
      mid: Number(col.mid),
      season_id: col.collection_type === 'season' ? Number(col.collection_id) : undefined,
      series_id: col.collection_type === 'series' ? Number(col.collection_id) : undefined,
      page: 1,
      page_size: 100
    });
    if (res && res.success && res.items && res.items.length > 0) {
      const vItems: VideoItem[] = res.items.map((arc: any) => ({
        bvid: arc.bvid,
        aid: arc.aid,
        视频名字: arc.title,
        UP名字: arc.author || selectedUpAuthor.value?.uname || "",
        发布日期: arc.pubdate || "",
        created: 0,
        URL: `https://www.bilibili.com/video/${arc.bvid}`,
        UP_MID: Number(col.mid),
        封面: arc.pic,
        时长: arc.duration
      }));
      await apiBatchDownload(vItems, selectedQuality.value, audioOnly.value, selectedAudioQuality.value);
      await pollTasks();
      showTaskDrawer.value = true;
    } else {
      showToast("未获取到合集视频，可能该合集为空或接口无返回");
    }
  } catch (e: any) {
    showToast("下载合集失败: " + (e.message || e));
  } finally {
    loadingUpCollections.value = false;
  }
}

// 点击 UP 主列表中
async function selectUp(mid: number) {
  if (selectedUpMid.value === mid) {
    selectedUpMid.value = null;
    upCollections.value = [];
    showUpCollectionsPanel.value = false;
  } else {
    selectedUpMid.value = mid;
    showUpCollectionsPanel.value = false;
    loadUpCollections(mid, false);
  }
  const shouldRefresh = settingRefreshOnSelect.value && selectedUpMid.value !== null;
  await loadVideos(shouldRefresh, !shouldRefresh);
}

const biliLoadError = ref<string>("");
const zhihuLoadError = ref<string>("");
const selectedBiliBvids = ref<string[]>([]);

function toggleSelectAllBiliVideos(selectAll: boolean) {
  if (!selectAll) {
    selectedBiliBvids.value = [];
    return;
  }
  selectedBiliBvids.value = filteredVideos.value
    .filter((v) => !isVideoRestricted(v) && getVideoDownloadState(v)?.status !== "success")
    .map((v) => v.bvid);
}

async function handleBatchDownloadSelectedBili() {
  if (selectedBiliBvids.value.length === 0) {
    showToast("请先勾选需要下载的 B站视频");
    return;
  }
  const selectedSet = new Set(selectedBiliBvids.value);
  const targets = filteredVideos.value.filter((v) => selectedSet.has(v.bvid) && !isVideoRestricted(v));
  if (targets.length === 0) {
    showToast("所选视频均为受限或不可下载内容");
    return;
  }
  try {
    await apiBatchDownload(targets, selectedQuality.value, audioOnly.value, selectedAudioQuality.value);
    selectedBiliBvids.value = [];
    await pollTasks();
    showToast(`已将所选 ${targets.length} 个${audioOnly.value ? "音频" : "视频"}加入下载队列`);
  } catch (err: any) {
    showToast("批量下载所选失败: " + (err.message || err));
  }
}

// 加载视频更新列表 (支持仅读本地 SQLite 缓存 vs 强制网络刷新)
async function loadVideos(forceRefresh: boolean = false, localOnly: boolean = false) {
  loadingVideos.value = true;
  biliLoadError.value = "";
  console.log(`[BiliFollow] 获取视频 (TagID: ${currentTagId.value}, 全量: ${isAll.value}, 强制刷新: ${forceRefresh}, 仅本地: ${localOnly}, 搜索: "${searchKeyword.value}", 单UP: ${selectedUpMid.value})...`);
  try {
    // 1. 先载入当前分组的博主列表
    const fRes = await apiGetFollowings(currentTagId.value, isAll.value, localOnly && !forceRefresh);
    bloggers.value = fRes.items || [];

    // 2. 发起视频查询请求
    const res = await apiGetVideosIndex(
      currentTagId.value,
      isAll.value,
      forceRefresh,
      searchKeyword.value,
      selectedUpMid.value || undefined,
      false,
      1,
      localOnly && !forceRefresh
    );
    videos.value = res.videos || [];
    totalVideos.value = res.total || 0;
    selectedBiliBvids.value = [];
    if (forceRefresh && settingAutoDownload.value) {
      await pollTasks();
    }
  } catch (err: any) {
    console.error("加载视频失败:", err);
    biliLoadError.value = err?.message || "加载B站视频列表失败，请检查登录状态或本地服务连接";
  } finally {
    loadingVideos.value = false;
  }
}

// -------------------------------- 收藏夹管理逻辑
async function loadFavFolders(forceRefresh: boolean = false) {
  try {
    const res = await apiGetFavFolders(forceRefresh);
    favFolders.value = res.folders || [];
    if (!currentFavId.value && favFolders.value.length > 0) {
      currentFavId.value = favFolders.value[0].id;
    }
  } catch (err: any) {
    console.error("加载收藏夹失败:", err);
    biliLoadError.value = err?.message || "加载B站收藏夹失败，请检查账号登录状态";
  }
}

async function switchFavFolder(fid: number) {
  currentFavId.value = fid;
  selectedBiliBvids.value = [];
  const shouldRefresh = settingRefreshOnSelect.value;
  await loadFavVideos(shouldRefresh, !shouldRefresh);
}

async function loadFavVideos(forceRefresh: boolean = false, localOnly: boolean = false) {
  if (!currentFavId.value) return;
  loadingVideos.value = true;
  biliLoadError.value = "";
  try {
    const res = await apiGetFavVideos(currentFavId.value, forceRefresh, searchKeyword.value, localOnly && !forceRefresh);
    videos.value = res.videos || [];
    totalVideos.value = res.total || 0;
    selectedBiliBvids.value = [];
    if (forceRefresh && settingAutoDownload.value) {
      await pollTasks();
    }
  } catch (err: any) {
    console.error("加载收藏夹视频失败:", err);
    biliLoadError.value = err?.message || "加载收藏夹视频失败，请检查网络连接或重试";
  } finally {
    loadingVideos.value = false;
  }
}

async function switchMode(mode: "follow" | "fav" | "link") {
  activeMode.value = mode;
  searchKeyword.value = "";
  selectedBiliBvids.value = [];
  biliLoadError.value = "";
  const shouldRefresh = settingRefreshOnSelect.value;
  if (mode === "fav") {
    if (favFolders.value.length === 0) {
      await loadFavFolders(shouldRefresh);
    }
    await loadFavVideos(shouldRefresh, !shouldRefresh);
  } else if (mode === "follow") {
    await loadVideos(shouldRefresh, !shouldRefresh);
  }
}

async function refreshCurrentView(forceRefresh: boolean = false) {
  if (activeMode.value === "fav") {
    await loadFavVideos(forceRefresh);
  } else if (activeMode.value === "follow") {
    await loadVideos(forceRefresh);
  }
}

// -------------------------------- Electron 官方窗口登录与受控会话
async function openOfficialLogin() {
  biliLoginTab.value = "qrcode";
  if (isElectron.value) {
    showElectronLoginModal.value = true;
    electronLoginStatus.value = "已启动哔哩哔哩官方登录窗口，请在弹出的窗口中扫码或输入密码登录...";
    try {
      const res = await (window as any).electronAPI.openBiliLogin();
      if (res && res.success) {
        showElectronLoginModal.value = false;
        await checkAuth();
        await loadAccounts();
        await refreshCurrentView(true);
      } else {
        electronLoginStatus.value = res.message || "登录未完成";
      }
    } catch (e: any) {
      electronLoginStatus.value = "打开登录窗口失败: " + e.message;
    }
  } else {
    await openQrLogin();
  }
}

async function handleManualVerifyLogin() {
  if (isElectron.value) {
    electronLoginStatus.value = "正在校验登录状态...";
    const res = await (window as any).electronAPI.verifyBiliLogin();
    if (res && res.success) {
      showElectronLoginModal.value = false;
      await checkAuth();
      await loadAccounts();
      await refreshCurrentView(true);
    } else {
      electronLoginStatus.value = res.message || "尚未检测到有效登录，请在网页中完成登录后重试";
    }
  }
}

async function handleCloseElectronLogin() {
  if (isElectron.value) {
    await (window as any).electronAPI.closeBiliLogin();
  }
  showElectronLoginModal.value = false;
}

// -------------------------------- 单 URL 与合集解析操作
async function handleParseLink() {
  if (!linkInput.value.trim()) {
    parseError.value = "请输入视频链接、分享文案或 BV 号";
    return;
  }
  parsingLink.value = true;
  parseError.value = "";
  parsedResult.value = null;
  try {
    const res = await apiParseUrl(linkInput.value.trim());
    parsedResult.value = res;
    selectedPartCids.value = res.parts.map((p) => p.cid);
    if (res.season?.episodes) {
      selectedEpisodeBvids.value = res.season.episodes.map((e) => e.bvid);
    }
  } catch (err: any) {
    parseError.value = err.message || "解析失败，请检查链接或网络";
  } finally {
    parsingLink.value = false;
  }
}

async function handleDownloadParsedVideo(audioOnlyFlag: boolean = true) {
  if (!parsedResult.value) return;
  const v = parsedResult.value;

  // 若为多 P 视频且有分 P 列表，按用户勾选的分 P 独立下发下载任务
  if (v.parts && v.parts.length > 1) {
    const chosenParts = v.parts.filter((p) => selectedPartCids.value.includes(p.cid));
    if (chosenParts.length === 0) {
      showToast("请至少勾选一个要下载的分 P！");
      return;
    }
    const partItems: VideoItem[] = chosenParts.map((p) => ({
      bvid: `${v.bvid}_P${p.page}`,
      real_bvid: v.bvid,
      cid: p.cid,
      part_page: p.page,
      part_title: p.part,
      aid: v.aid,
      视频名字: `${v.title} - P${p.page} ${p.part}`,
      UP名字: v.author,
      发布日期: v.pubdate || "",
      created: 0,
      URL: `${v.url}?p=${p.page}`,
      UP_MID: v.up_mid,
      封面: v.pic,
      时长: p.duration || v.duration
    }));
    await apiBatchDownload(partItems, selectedQuality.value, audioOnlyFlag, selectedAudioQuality.value);
    await pollTasks();
    showTaskDrawer.value = true;
    return;
  }

  const videoItem: VideoItem = {
    bvid: v.bvid,
    aid: v.aid,
    视频名字: v.title,
    UP名字: v.author,
    发布日期: v.pubdate || "",
    created: 0,
    URL: v.url,
    UP_MID: v.up_mid,
    封面: v.pic,
    时长: v.duration
  };
  await apiDownloadSingle(videoItem, selectedQuality.value, audioOnlyFlag, selectedAudioQuality.value);
  await pollTasks();
  showTaskDrawer.value = true;
}

function toggleSelectAllParts(selectAll: boolean) {
  if (!parsedResult.value?.parts) return;
  selectedPartCids.value = selectAll ? parsedResult.value.parts.map((p) => p.cid) : [];
}

function toggleSelectAllEpisodes(selectAll: boolean) {
  if (!parsedResult.value?.season?.episodes) return;
  selectedEpisodeBvids.value = selectAll ? parsedResult.value.season.episodes.map((e) => e.bvid) : [];
}

async function handleDownloadSelectedEpisodes(audioOnlyFlag: boolean = true) {
  if (!parsedResult.value?.season?.episodes) return;
  const eps = parsedResult.value.season.episodes.filter((e) =>
    selectedEpisodeBvids.value.includes(e.bvid)
  );
  if (eps.length === 0) {
    showToast("请至少勾选一个合集视频！");
    return;
  }
  const videoItems: VideoItem[] = eps.map((e) => ({
    bvid: e.bvid,
    aid: e.aid,
    视频名字: e.title,
    UP名字: parsedResult.value?.author || "未知UP主",
    发布日期: e.pubdate || "",
    created: 0,
    URL: `https://www.bilibili.com/video/${e.bvid}`,
    UP_MID: parsedResult.value?.up_mid || 0,
    封面: e.pic,
    时长: e.duration
  }));
  await apiBatchDownload(videoItems, selectedQuality.value, audioOnlyFlag, selectedAudioQuality.value);
  await pollTasks();
  showTaskDrawer.value = true;
}

// 本地免关注订阅博主
async function handleSubscribeLocalUp() {
  const txt = localSubInput.value.trim();
  if (!txt) return;
  subscribingUp.value = true;
  try {
    const targetTag = isAll.value ? -10 : currentTagId.value;
    const res = await apiSubscribeLocalUp(txt, targetTag);
    localSubInput.value = "";
    showToast(res.message);
    if (res.up?.mid) {
      selectedUpMid.value = Number(res.up.mid);
    }
    await loadVideos(true);
  } catch (err: any) {
    showToast("添加本地订阅失败: " + err.message);
  } finally {
    subscribingUp.value = false;
  }
}

// 单博主深度抓取全部历史作品
async function handleDeepScrapeUp() {
  if (!selectedUpMid.value) return;
  deepScraping.value = true;
  loadingVideos.value = true;
  try {
    const res = await apiGetVideosIndex(
      currentTagId.value,
      isAll.value,
      true,
      searchKeyword.value,
      selectedUpMid.value,
      true,
      25
    );
    videos.value = res.videos || [];
    totalVideos.value = res.total || 0;
  } catch (err: any) {
    showToast("深度抓取历史稿件失败: " + err.message);
  } finally {
    deepScraping.value = false;
    loadingVideos.value = false;
  }
}

// 立即手动执行一次后台追更巡检
async function handleRunAutoCheckNow() {
  runningManualCheck.value = true;
  try {
    const res = await apiRunAutoCheckNow();
    if (res.state) {
      autoCheckStatus.value = res.state;
    }
    await loadVideos(false);
    await pollTasks();
    showToast(res.message);
  } catch (err: any) {
    showToast("手动巡检失败: " + err.message);
  } finally {
    runningManualCheck.value = false;
  }
}

// 标为已读
async function handleMarkRead(bvid?: string) {
  try {
    await apiMarkVideosRead(bvid);
    if (bvid) {
      const target = videos.value.find((x) => x.bvid === bvid);
      if (target) target.is_read = 1;
    } else {
      videos.value.forEach((x) => {
        x.is_read = 1;
      });
    }
  } catch (_) {}
}

// 触发搜索与防抖统一过滤
let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null;

function handleSearch() {
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = null;
  }
  if (activePlatform.value === "zhihu") {
    loadZhihuContents();
  } else if (activePlatform.value === "bili") {
    refreshCurrentView(false);
  }
}

function clearGlobalSearch() {
  searchKeyword.value = "";
  handleSearch();
}

watch(searchKeyword, () => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    if (activePlatform.value === "zhihu") {
      loadZhihuContents();
    } else if (activePlatform.value === "bili") {
      refreshCurrentView(false);
    }
  }, 260);
});

// 同步画质与音质默认偏好到后端
function handleQualityPreferenceChange() {
  if (activePlatform.value === "settings") {
    settingsDirty.value = true;
    return;
  }
  captureSettingsSnapshot();
  apiUpdateSettings({
    default_video_quality: selectedQuality.value,
    default_audio_quality: selectedAudioQuality.value,
  }).catch(() => {});
}

// 触发单个视频下载
async function triggerDownload(video: VideoItem) {
  if (isVideoRestricted(video)) {
    showToast(`该视频属于【${getVideoRestrictReason(video)}】，已开启付费/受限视频保护拦截，禁止下载。`);
    return;
  }
  try {
    if (video.is_read === 0) {
      handleMarkRead(video.bvid);
    }
    await apiDownloadSingle(video, selectedQuality.value, audioOnly.value, selectedAudioQuality.value);
    await pollTasks();
  } catch (err: any) {
    showToast("下载启动失败: " + err.message);
  }
}

// 批量下载当前列表中未下载的视频/音频
async function handleBatchDownload() {
  const targets = undownloadedVideos.value;
  if (targets.length === 0) {
    showToast("当前列表中所有可用视频均已下载完成（受限付费视频已自动跳过）！");
    return;
  }
  if (!confirm(`确定要开始批量下载当前列表中的 ${targets.length} 个${audioOnly.value ? "音频 (.m4a)" : "视频"} 吗？`)) {
    return;
  }
  try {
    await apiBatchDownload(targets, selectedQuality.value, audioOnly.value, selectedAudioQuality.value);
    await pollTasks();
  } catch (err: any) {
    showToast("批量下载启动失败: " + err.message);
  }
}

// 重试单项
async function handleRetrySingle(bvid: string) {
  try {
    await apiRetryDownload(bvid);
    await pollTasks();
  } catch (e: any) {
    showToast("重试失败: " + e.message);
  }
}

// 取消单项下载
async function handleCancelSingle(bvid: string) {
  try {
    await apiCancelDownload(bvid);
    await pollTasks();
  } catch (e: any) {
    showToast("取消下载失败: " + e.message);
  }
}

// 重试当前筛选范围内的失败任务（不包含已取消任务）
async function handleRetryAllFailed() {
  const ids = scopedFailedTaskIds.value;
  if (ids.length === 0) {
    showToast("当前筛选范围内没有失败任务");
    return;
  }
  try {
    const res = await apiRetryDownload(undefined, false, ids, false, false);
    showToast(res.message || `已重新提交 ${ids.length} 个失败任务`);
    await pollTasks();
  } catch (e: any) {
    showToast("重试失败: " + e.message);
  }
}

// 单独恢复当前筛选范围内的已取消任务
async function handleRestoreCancelled() {
  const ids = scopedCancelledTaskIds.value;
  if (ids.length === 0) {
    showToast("当前筛选范围内没有已取消任务");
    return;
  }
  try {
    const res = await apiRetryDownload(undefined, false, ids, false, true);
    showToast(res.message || `已恢复 ${ids.length} 个已取消任务`);
    await pollTasks();
  } catch (e: any) {
    showToast("恢复任务失败: " + e.message);
  }
}

// 仅清除当前筛选范围内的已完成任务（不清除已取消或受限记录）
async function handleClearCompleted() {
  const ids = scopedCompletedTaskIds.value;
  if (ids.length === 0) {
    showToast("当前筛选范围内没有已完成记录可清理");
    return;
  }
  try {
    const res = await apiClearCompletedDownloads(ids, ["success"]);
    showToast(`已从列表清除 ${res.count ?? ids.length} 条已完成记录`);
    await pollTasks();
  } catch (e: any) {
    showToast("清理失败: " + e.message);
  }
}

// 磁盘对账自愈扫描
async function handleManualReconcile() {
  isReconciling.value = true;
  reconcileNotice.value = "";
  try {
    const res = await apiReconcileDownloads();
    reconcileNotice.value = res.message;
    await pollTasks();
    await loadVideos(false);
    setTimeout(() => {
      reconcileNotice.value = "";
    }, 4500);
  } catch (e: any) {
    showToast("检查本地文件失败: " + e.message);
  } finally {
    isReconciling.value = false;
  }
}

// 设置页脏状态保护与统一保存规则
const settingsDirty = ref<boolean>(false);
const limiterDirty = ref<boolean>(false);
let suppressSettingsWatch = false;

let savedSettingsSnapshot = {
  download_dir: settingDownloadDir.value,
  download_max_workers: Number(settingWorkers.value) || 3,
  refresh_on_select: !!settingRefreshOnSelect.value,
  auto_check_enabled: !!settingAutoCheck.value,
  auto_check_interval: Number(settingCheckInterval.value) || 30,
  auto_check_scope: settingCheckScope.value,
  auto_download_new: !!settingAutoDownload.value,
  default_video_quality: String(selectedQuality.value || "1080P"),
  default_audio_quality: String(selectedAudioQuality.value || "30280"),
  block_paid_videos: !!settingBlockPaidVideos.value,
};

let savedLimiterSnapshot: RateLimiterConfig = { ...limiterForm.value };

function captureSettingsSnapshot() {
  savedSettingsSnapshot = {
    download_dir: settingDownloadDir.value,
    download_max_workers: Number(settingWorkers.value) || 3,
    refresh_on_select: !!settingRefreshOnSelect.value,
    auto_check_enabled: !!settingAutoCheck.value,
    auto_check_interval: Number(settingCheckInterval.value) || 30,
    auto_check_scope: settingCheckScope.value,
    auto_download_new: !!settingAutoDownload.value,
    default_video_quality: String(selectedQuality.value || "1080P"),
    default_audio_quality: String(selectedAudioQuality.value || "30280"),
    block_paid_videos: !!settingBlockPaidVideos.value,
  };
}

function captureLimiterSnapshot() {
  savedLimiterSnapshot = { ...limiterForm.value };
}

function hasGeneralSettingsDiff(): boolean {
  return (
    settingDownloadDir.value !== savedSettingsSnapshot.download_dir ||
    Number(settingWorkers.value) !== savedSettingsSnapshot.download_max_workers ||
    !!settingRefreshOnSelect.value !== savedSettingsSnapshot.refresh_on_select ||
    !!settingAutoCheck.value !== savedSettingsSnapshot.auto_check_enabled ||
    Number(settingCheckInterval.value) !== savedSettingsSnapshot.auto_check_interval ||
    settingCheckScope.value !== savedSettingsSnapshot.auto_check_scope ||
    !!settingAutoDownload.value !== savedSettingsSnapshot.auto_download_new ||
    String(selectedQuality.value) !== savedSettingsSnapshot.default_video_quality ||
    String(selectedAudioQuality.value) !== savedSettingsSnapshot.default_audio_quality ||
    !!settingBlockPaidVideos.value !== savedSettingsSnapshot.block_paid_videos
  );
}

function hasLimiterDiff(): boolean {
  return (
    Number(limiterForm.value.rate) !== Number(savedLimiterSnapshot.rate) ||
    Number(limiterForm.value.capacity) !== Number(savedLimiterSnapshot.capacity) ||
    Number(limiterForm.value.min_interval) !== Number(savedLimiterSnapshot.min_interval) ||
    Number(limiterForm.value.jitter) !== Number(savedLimiterSnapshot.jitter) ||
    Number(limiterForm.value.ban_cooldown) !== Number(savedLimiterSnapshot.ban_cooldown) ||
    Number(limiterForm.value.max_retries) !== Number(savedLimiterSnapshot.max_retries)
  );
}

function restoreSavedSettingsSnapshot() {
  suppressSettingsWatch = true;
  settingDownloadDir.value = savedSettingsSnapshot.download_dir;
  settingWorkers.value = savedSettingsSnapshot.download_max_workers;
  settingRefreshOnSelect.value = savedSettingsSnapshot.refresh_on_select;
  localStorage.setItem("gatherbox_refresh_on_select", String(settingRefreshOnSelect.value));
  settingAutoCheck.value = savedSettingsSnapshot.auto_check_enabled;
  settingCheckInterval.value = savedSettingsSnapshot.auto_check_interval;
  settingCheckScope.value = savedSettingsSnapshot.auto_check_scope;
  settingAutoDownload.value = savedSettingsSnapshot.auto_download_new;
  selectedQuality.value = savedSettingsSnapshot.default_video_quality;
  selectedAudioQuality.value = savedSettingsSnapshot.default_audio_quality;
  settingBlockPaidVideos.value = savedSettingsSnapshot.block_paid_videos;
  limiterForm.value = { ...savedLimiterSnapshot };
  settingsDirty.value = false;
  limiterDirty.value = false;
  nextTick(() => {
    suppressSettingsWatch = false;
  });
}

watch(
  [
    settingWorkers,
    settingDownloadDir,
    selectedQuality,
    selectedAudioQuality,
    settingBlockPaidVideos,
    settingRefreshOnSelect,
    settingAutoCheck,
    settingCheckInterval,
    settingCheckScope,
    settingAutoDownload,
  ],
  () => {
    if (!suppressSettingsWatch && activePlatform.value === "settings") {
      settingsDirty.value = hasGeneralSettingsDiff() || limiterDirty.value;
    }
  }
);

watch(
  limiterForm,
  () => {
    if (!suppressSettingsWatch && activePlatform.value === "settings" && activeSettingsTab.value === "limiter") {
      limiterDirty.value = hasLimiterDiff();
      settingsDirty.value = hasGeneralSettingsDiff() || limiterDirty.value;
    }
  },
  { deep: true }
);

function confirmLeaveSettings(): boolean {
  if (activePlatform.value === "settings" && (settingsDirty.value || limiterDirty.value)) {
    if (!confirm("当前设置页有未保存的修改，确定要放弃更改并离开吗？")) {
      return false;
    }
    restoreSavedSettingsSnapshot();
  }
  return true;
}

function switchSettingsTab(tab: "download" | "autocheck" | "limiter" | "appearance" | "about") {
  activeSettingsTab.value = tab;
  if (tab === "about" && !updateInfo.value) {
    checkAppUpdate(false);
  }
}

// 打开设置中心
async function openSettings(tab: "download" | "autocheck" | "limiter" | "appearance" | "about" = "download") {
  if (activePlatform.value === "settings" && (settingsDirty.value || limiterDirty.value)) {
    switchSettingsTab(tab);
    return;
  }
  if (activePlatform.value !== "settings") {
    previousPlatform.value = activePlatform.value as any;
  }
  activePlatform.value = "settings";
  activeSettingsTab.value = tab;
  showSettingsModal.value = false;
  suppressSettingsWatch = true;
  try {
    const res = await apiGetSettings();
    appSettings.value = res;
    settingDownloadDir.value = res.download_dir;
    settingWorkers.value = res.download_max_workers || 3;
    settingRefreshOnSelect.value = !!res.refresh_on_select;
    localStorage.setItem("gatherbox_refresh_on_select", String(settingRefreshOnSelect.value));
    settingAutoCheck.value = res.auto_check_enabled || false;
    settingCheckInterval.value = res.auto_check_interval || 30;
    settingCheckScope.value = res.auto_check_scope || "special";
    settingAutoDownload.value = res.auto_download_new || false;
    if (res.default_video_quality) selectedQuality.value = res.default_video_quality;
    if (res.default_audio_quality) selectedAudioQuality.value = res.default_audio_quality;
    if (res.block_paid_videos !== undefined) settingBlockPaidVideos.value = !!res.block_paid_videos;
    captureSettingsSnapshot();
    apiGetAutoCheckStatus().then((st) => {
      autoCheckStatus.value = st;
    }).catch(() => {});
    await loadAccounts();
    const limRes = await apiGetLimiterStatus(selectedLimiterPlatform.value);
    limiterStatus.value = limRes;
    limiterForm.value = { ...limRes.config };
    captureLimiterSnapshot();
    settingsDirty.value = false;
    limiterDirty.value = false;
    if (tab === "about" && !updateInfo.value) {
      checkAppUpdate(false);
    }
  } catch (err: any) {
    settingsSaveNotice.value = "读取设置失败: " + err.message;
  } finally {
    nextTick(() => {
      suppressSettingsWatch = false;
    });
  }
}

// 保存统一设置
async function saveAllSettings() {
  suppressSettingsWatch = true;
  try {
    localStorage.setItem("gatherbox_refresh_on_select", String(settingRefreshOnSelect.value));
    const trimmedDir = settingDownloadDir.value.trim();
    const res = await apiUpdateSettings({
      download_dir: trimmedDir,
      download_max_workers: Number(settingWorkers.value),
      refresh_on_select: settingRefreshOnSelect.value,
      auto_check_enabled: settingAutoCheck.value,
      auto_check_interval: Number(settingCheckInterval.value),
      auto_check_scope: settingCheckScope.value,
      auto_download_new: settingAutoDownload.value,
      default_video_quality: selectedQuality.value,
      default_audio_quality: selectedAudioQuality.value,
      block_paid_videos: settingBlockPaidVideos.value,
      theme: themeMode.value,
    });
    appSettings.value = res.settings;
    settingDownloadDir.value = trimmedDir;
    captureSettingsSnapshot();

    if (activeSettingsTab.value === "limiter" || limiterDirty.value) {
      const limiterRes = await apiUpdateLimiterConfig({
        ...limiterForm.value,
        platform: selectedLimiterPlatform.value,
      });
      limiterStatus.value.config = limiterRes.config;
      limiterForm.value = { ...limiterRes.config };
      captureLimiterSnapshot();
    }

    settingsDirty.value = false;
    limiterDirty.value = false;
    settingsSaveNotice.value = "系统配置已保存并立即生效！";
    setTimeout(() => { settingsSaveNotice.value = ""; }, 3000);
  } catch (err: any) {
    settingsSaveNotice.value = "保存设置失败: " + err.message;
    setTimeout(() => { settingsSaveNotice.value = ""; }, 4000);
  } finally {
    nextTick(() => {
      suppressSettingsWatch = false;
    });
  }
}

// 接口限流平台切换与调度方法
const selectedLimiterPlatform = ref<"bili" | "zhihu">("bili");

async function switchLimiterPlatform(plat: "bili" | "zhihu") {
  if (limiterDirty.value) {
    if (!confirm("当前平台限流参数已修改但尚未保存，切换平台将放弃修改，确定继续吗？")) {
      return;
    }
    limiterDirty.value = false;
    settingsDirty.value = hasGeneralSettingsDiff();
  }
  selectedLimiterPlatform.value = plat;
  suppressSettingsWatch = true;
  try {
    const res = await apiGetLimiterStatus(plat);
    limiterStatus.value = res;
    limiterForm.value = { ...res.config };
    captureLimiterSnapshot();
  } catch (_) {
  } finally {
    nextTick(() => {
      suppressSettingsWatch = false;
    });
  }
}

async function pollLimiter() {
  try {
    const res = await apiGetLimiterStatus(selectedLimiterPlatform.value);
    limiterStatus.value = res;
    const isEditingLimiter = activePlatform.value === "settings" && activeSettingsTab.value === "limiter";
    if (!isEditingLimiter && !limiterDirty.value) {
      suppressSettingsWatch = true;
      limiterForm.value = { ...res.config };
      captureLimiterSnapshot();
      nextTick(() => {
        suppressSettingsWatch = false;
      });
    }
  } catch (_) {}
}

function startLimiterPolling() {
  pollLimiter();
  limiterPollTimer = setInterval(pollLimiter, 1200);
}

function applyPreset(name: "safe" | "standard" | "fast" | "turbo") {
  const isZhihu = selectedLimiterPlatform.value === "zhihu";
  if (name === "safe") {
    limiterForm.value.rate = isZhihu ? 0.2 : 0.3;
    limiterForm.value.capacity = isZhihu ? 1.5 : 2.0;
    limiterForm.value.min_interval = isZhihu ? 3.0 : 2.5;
    limiterForm.value.jitter = 0.8;
    limiterForm.value.ban_cooldown = isZhihu ? 35.0 : 25.0;
    limiterForm.value.max_retries = 4;
  } else if (name === "standard") {
    limiterForm.value.rate = isZhihu ? 0.6 : 0.8;
    limiterForm.value.capacity = isZhihu ? 2.0 : 3.0;
    limiterForm.value.min_interval = isZhihu ? 1.5 : 1.0;
    limiterForm.value.jitter = isZhihu ? 0.5 : 0.4;
    limiterForm.value.ban_cooldown = isZhihu ? 30.0 : 15.0;
    limiterForm.value.max_retries = 3;
  } else if (name === "fast") {
    limiterForm.value.rate = isZhihu ? 1.0 : 1.5;
    limiterForm.value.capacity = isZhihu ? 3.0 : 5.0;
    limiterForm.value.min_interval = isZhihu ? 1.0 : 0.6;
    limiterForm.value.jitter = 0.3;
    limiterForm.value.ban_cooldown = isZhihu ? 20.0 : 12.0;
    limiterForm.value.max_retries = 3;
  } else if (name === "turbo") {
    limiterForm.value.rate = isZhihu ? 1.5 : 3.0;
    limiterForm.value.capacity = isZhihu ? 4.0 : 6.0;
    limiterForm.value.min_interval = isZhihu ? 0.6 : 0.2;
    limiterForm.value.jitter = 0.2;
    limiterForm.value.ban_cooldown = isZhihu ? 15.0 : 10.0;
    limiterForm.value.max_retries = 2;
  }
}

async function toggleLimiterPause() {
  try {
    const res = await apiToggleLimiterPause(undefined, selectedLimiterPlatform.value);
    limiterStatus.value.is_paused = res.is_paused;
  } catch (e: any) {
    showToast("操作失败: " + e.message);
  }
}

async function resetLimiterStats() {
  const platName = selectedLimiterPlatform.value === "zhihu" ? "知乎" : "B站";
  if (confirm(`确定要重置 ${platName} 请求统计计数与历史日志吗？`)) {
    await apiResetLimiterStats(selectedLimiterPlatform.value);
    await pollLimiter();
  }
}

// ---------------- 任务队列维度分组状态与计算属性 ----------------
const taskGroupingMode = ref<"flat" | "account" | "author" | "folder">("flat");
const collapsedGroups = ref<Record<string, boolean>>({});

interface TaskGroupItem {
  groupKey: string;
  groupName: string;
  groupType: "account" | "author" | "folder";
  platform: "bili" | "zhihu";
  tasks: DownloadTaskItem[];
  total: number;
  success: number;
  running: number;
  failed: number;
}

const groupedTasks = computed<TaskGroupItem[]>(() => {
  const list = filteredDrawerTasks.value;
  if (taskGroupingMode.value === "flat") return [];

  const map: Record<string, TaskGroupItem> = {};
  for (const t of list) {
    let key = "";
    let name = "";
    let gType: "account" | "author" | "folder" = "author";
    const plat = t.platform || (t.bvid.startsWith("zhihu_") ? "zhihu" : "bili");

    if (taskGroupingMode.value === "account") {
      name = t.account_name || (plat === "zhihu" ? zhihuUser.value.uname || "知乎账号" : user.value.uname || "B站账号");
      key = `${plat}_account_${name}`;
      gType = "account";
    } else if (taskGroupingMode.value === "author") {
      name = t.author || (plat === "zhihu" ? "知乎答主" : "未知UP主");
      key = `${plat}_author_${name}`;
      gType = "author";
    } else {
      name = t.group_name || (plat === "zhihu" ? "知乎收藏夹/合集" : "B站收藏夹");
      key = `${plat}_folder_${name}`;
      gType = "folder";
    }

    if (!map[key]) {
      map[key] = {
        groupKey: key,
        groupName: name,
        groupType: gType,
        platform: plat,
        tasks: [],
        total: 0,
        success: 0,
        running: 0,
        failed: 0
      };
    }

    map[key].tasks.push(t);
    map[key].total += 1;
    if (t.status === "success") map[key].success += 1;
    else if (t.status === "downloading" || t.status === "pending") map[key].running += 1;
    else if (t.status === "failed") map[key].failed += 1;
  }

  return Object.values(map);
});

function toggleGroupCollapse(key: string) {
  collapsedGroups.value[key] = !collapsedGroups.value[key];
}

async function handleRetryGroup(grp: TaskGroupItem) {
  const failed = grp.tasks.filter((t) => t.status === "failed" && t.can_retry !== false && !t.is_stopping);
  for (const f of failed) {
    try {
      await apiRetryDownload(f.task_key || f.bvid);
    } catch (_) {}
  }
  await pollTasks();
}

// 打开本地文件与目录
async function openFolder(filePath?: string) {
  await apiOpenFolder(filePath);
}

const isSelectingFolder = ref(false);
async function handlePickDownloadDir() {
  if (isSelectingFolder.value) return;
  isSelectingFolder.value = true;
  try {
    const res = await apiSelectFolder(settingDownloadDir.value);
    if (res.success && !res.cancelled && res.path) {
      suppressSettingsWatch = true;
      settingDownloadDir.value = res.path;
      const saved = await apiUpdateSettings({ download_dir: res.path });
      appSettings.value = saved.settings;
      captureSettingsSnapshot();
      settingsDirty.value = hasGeneralSettingsDiff() || limiterDirty.value;
      settingsSaveNotice.value = "下载位置已更新，将按 bilibili / zhihu 平台一级目录自动归类！";
      setTimeout(() => { settingsSaveNotice.value = ""; }, 3500);
    } else if (!res.success && res.message) {
      showToast(res.message);
    }
  } catch (err: any) {
    showToast("选择目录失败: " + (err?.message || err));
  } finally {
    isSelectingFolder.value = false;
    nextTick(() => {
      suppressSettingsWatch = false;
    });
  }
}

async function openFile(filePath?: string) {
  if (filePath) await apiOpenFile(filePath);
}

// 封面图片加载失败时回退防盗链代理
function onImgError(e: Event, originalPic?: string) {
  const img = e.target as HTMLImageElement;
  if (!img) return;
  const proxy = getProxyPicUrl(originalPic);
  if (img.src !== proxy) {
    img.src = proxy;
  } else {
    img.src = "https://static.hdslb.com/images/akari.jpg";
  }
}

// 轮询下载任务状态
async function pollTasks() {
  try {
    const res = await apiGetDownloadTasks();
    allTasks.value = res.tasks || [];
    activeDownloadCount.value = res.active_count || 0;
  } catch (_) {}
}

function startTaskPolling() {
  pollTasks();
  taskPollTimer = setInterval(pollTasks, 1500);
}

// 打开扫码登录弹窗
async function openQrLogin() {
  showQrModal.value = true;
  rememberAndFocusModal('.modal-card[role="dialog"]');
  qrStatusText.value = "正在申请二维码...";
  qrImg.value = "";
  try {
    const res = await apiGenerateQrCode();
    qrKey.value = res.qrcode_key;
    qrImg.value = res.qrcode_img;
    qrStatusText.value = "请打开 哔哩哔哩 手机客户端扫码";

    if (qrPollTimer) clearInterval(qrPollTimer);
    qrPollTimer = setInterval(async () => {
      try {
        const poll = await apiPollQrCode(qrKey.value);
        if (poll.status === "waiting") {
          qrStatusText.value = "等待扫码中...";
        } else if (poll.status === "scanned") {
          qrStatusText.value = "已扫码，请在手机上点击确认！";
        } else if (poll.status === "success") {
          qrStatusText.value = "登录成功！正在同步数据...";
          clearInterval(qrPollTimer);
          setTimeout(() => {
            showQrModal.value = false;
            checkAuth();
            loadAccounts();
          }, 1200);
        } else if (poll.status === "expired") {
          qrStatusText.value = "二维码已失效，请点击刷新";
          clearInterval(qrPollTimer);
        }
      } catch (e) {
        clearInterval(qrPollTimer);
      }
    }, 2000);
  } catch (err: any) {
    qrStatusText.value = "获取二维码失败: " + err.message;
  }
}

function closeQrModal() {
  showQrModal.value = false;
  if (qrPollTimer) clearInterval(qrPollTimer);
}

// 手动导入 Cookie
async function handleImportCookie() {
  if (!cookieInput.value.trim()) {
    cookieError.value = "请输入 Cookie 字符串";
    return;
  }
  cookieLoading.value = true;
  cookieError.value = "";
  try {
    await apiImportCookie(cookieInput.value.trim());
    showCookieModal.value = false;
    cookieInput.value = "";
    await checkAuth();
    await loadAccounts();
  } catch (err: any) {
    cookieError.value = err.message || "Cookie 校验失败";
  } finally {
    cookieLoading.value = false;
  }
}

// 退出登录
async function handleLogout() {
  if (confirm("确定要退出登录吗？")) {
    await apiLogout();
    user.value = { is_login: false };
    videos.value = [];
    tags.value = [];
  }
}

// 在浏览器中打开视频网页
function openInBrowser(url: string) {
  void openExternalUrl(url);
}

// ==================== 知乎多平台与四栏双级导航状态 ====================
const primarySidebarCollapsed = ref<boolean>(false);
const zhihuGroupExpanded = ref<boolean>(true);
const biliGroupExpanded = ref<boolean>(true);
const activePlatform = ref<"zhihu" | "bili" | "tasks" | "accounts" | "settings">("zhihu");
const previousPlatform = ref<"zhihu" | "bili" | "tasks" | "accounts">("zhihu");
const checkingUpdate = ref<boolean>(false);
const updateInfo = ref<UpdateCheckResult | null>(null);
const updateError = ref<string>("");
const showUpdateModal = ref<boolean>(false);
const updateNoticeMsg = ref<string>("");
const updateDownloadState = ref<UpdateDownloadStatus>({
  status: "idle",
  progress: 0,
  downloaded_bytes: 0,
  total_bytes: 0,
  speed_text: "",
  file_name: "",
  file_path: "",
  error: "",
  updated_at: 0,
});
let updateDownloadPollTimer: ReturnType<typeof setInterval> | null = null;
const settingAutoCheckUpdate = ref<boolean>(true);
const settingsSaveNotice = ref<string>("");
const uiScale = ref<number>(Number(localStorage.getItem("gatherbox_ui_scale") || "1.0"));

function applyUiScale() {
  const validScale = Math.min(1.4, Math.max(0.8, Number(uiScale.value) || 1.0));
  uiScale.value = Number(validScale.toFixed(2));
  document.documentElement.style.setProperty("--ui-scale", String(uiScale.value));
  document.documentElement.style.zoom = String(uiScale.value);
  localStorage.setItem("gatherbox_ui_scale", String(uiScale.value));
  handleWindowResize();
}

function resetUiScale() {
  uiScale.value = 1.0;
  applyUiScale();
}

function formatUpdateBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / 1024).toFixed(0)} KB`;
}

async function checkAppUpdate(force: boolean = false, autoPopup: boolean = false) {
  checkingUpdate.value = true;
  updateError.value = "";
  try {
    const res = await apiCheckUpdate(force);
    updateInfo.value = res;
    if (!res.success && res.message) {
      updateError.value = res.message;
    } else if (res.has_update && (autoPopup || force)) {
      showUpdateModal.value = true;
    }
  } catch (err: any) {
    updateError.value = err?.message || "网络请求失败，请检查网络连接";
  } finally {
    checkingUpdate.value = false;
  }
}

function stopUpdateDownloadPolling() {
  if (updateDownloadPollTimer) {
    clearInterval(updateDownloadPollTimer);
    updateDownloadPollTimer = null;
  }
}

async function startInAppUpdateDownload() {
  if (!updateInfo.value) return;
  const dlUrl = updateInfo.value.download_url || "";
  if (!dlUrl || dlUrl.endsWith("/releases")) {
    openExternalUrl(updateInfo.value.html_url || "https://github.com/joelovechen/GatherBox_Lite/releases");
    return;
  }
  updateNoticeMsg.value = "";
  updateError.value = "";
  try {
    const st = await apiStartUpdateDownload(dlUrl, updateInfo.value.asset_name || "", true);
    updateDownloadState.value = st;
    stopUpdateDownloadPolling();
    updateDownloadPollTimer = setInterval(async () => {
      try {
        const cur = await apiGetUpdateDownloadStatus();
        updateDownloadState.value = cur;
        if (cur.status === "completed") {
          stopUpdateDownloadPolling();
          if (cur.error) {
            updateError.value = cur.error;
          } else {
            updateNoticeMsg.value = "下载已完成并自动拉起安装向导，请按提示完成安装";
          }
        } else if (cur.status === "failed") {
          stopUpdateDownloadPolling();
          updateError.value = cur.error || "更新包下载失败，可点击右侧打开发布页手动下载";
        }
      } catch (_) {}
    }, 350);
  } catch (err: any) {
    updateError.value = err?.message || "启动下载失败，请尝试打开发布页下载";
  }
}

async function triggerInstallUpdate(revealInFolder: boolean = false) {
  try {
    const res = await apiLaunchUpdateInstaller(updateDownloadState.value.file_path, revealInFolder);
    updateNoticeMsg.value = res.message;
  } catch (err: any) {
    updateError.value = err?.message || "启动安装程序失败";
  }
}

async function openExternalUrl(url: string) {
  try {
    const target = new URL(url);
    if (!['http:', 'https:'].includes(target.protocol)) throw new Error('仅支持网页链接');
    if ('__TAURI_INTERNALS__' in window) {
      const { openUrl } = await import('@tauri-apps/plugin-opener');
      await openUrl(target.href);
    } else if (!window.open(target.href, '_blank')) {
      throw new Error('浏览器阻止了新窗口，请允许弹出窗口后重试');
    }
  } catch (e: any) {
    showToast('打开链接失败: ' + (e.message || '链接无效'));
  }
}


function closeSettings() {
  if (!confirmLeaveSettings()) return;
  activePlatform.value = previousPlatform.value || 'zhihu';
}

function getSettingsTabTitle(tab: string): string {
  switch (tab) {
    case 'download': return '下载与存储';
    case 'autocheck': return '刷新与下载';
    case 'limiter': return '请求频率';
    case 'appearance': return '外观';
    case 'about': return '关于与更新';
    default: return '系统设置';
  }
}

const zhihuModule = ref<"creators" | "collections" | "upvotes" | "single">("creators");
const showRightPanel = ref<boolean>(false);
const rightPanelTab = ref<"reader" | "queue">("reader");
const taskViewSubTab = ref<"all" | "running" | "success" | "failed" | "logs">("all");
const systemLogFile = ref<string>("");
const systemLogLines = ref<string[]>([]);
const loadingSystemLogs = ref<boolean>(false);

const zhihuUser = ref<ZhihuAuthInfo>({
  is_login: false,
  url_token: "",
  uname: "未登录",
  face: ""
});
const showZhihuLoginModal = ref<boolean>(false);
const zhihuLoginTab = ref<"qrcode" | "browser" | "cookie">("qrcode");
const zhihuQrImg = ref<string>("");
const zhihuQrKey = ref<string>("");
const zhihuQrStatus = ref<string>("正在申请知乎二维码...");
const zhihuQrBlocked = ref<boolean>(false);
const zhihuBrowserLoggingIn = ref<boolean>(false);
let zhihuQrPollTimer: any = null;
let zhihuQrPollingActive = false;
let zhihuLoginGeneration = 0;
let zhihuBrowserPollTimer: any = null;
const zhihuCookieInput = ref<string>("");
const zhihuCookieError = ref<string>("");
const zhihuCookieLoading = ref<boolean>(false);

const zhihuSummary = ref<ZhihuSummaryCounts>({
  creator_unread: 0,
  collection_unread: 0,
  upvote_unread: 0,
  total_unread: 0,
  creator_total: 0,
  collection_total: 0,
  upvote_total: 0,
  archived_total: 0
});

// 1. 知乎博主监听状态
const zhihuCreators = ref<ZhihuCreatorItem[]>([]);
const selectedZhihuCreator = ref<string>("all");
const newCreatorInput = ref<string>("");
const addingCreator = ref<boolean>(false);
const syncingAccountCreators = ref<boolean>(false);

// 2. 知乎收藏夹监听状态
const zhihuCollections = ref<ZhihuCollectionItem[]>([]);
const zhihuCollectionFilter = ref<string>("all");
const selectedZhihuCollection = ref<string>("all");
const newCollectionInput = ref<string>("");
const addingCollection = ref<boolean>(false);
const syncingAccountCollections = ref<boolean>(false);

// 3. 知乎我赞同的帖子状态
const upvoteBindToken = ref<string>("");
const latestUpvoteTime = ref<number>(0);

// 4. 知乎单帖/问题采集与内容列表状态
const zhihuSingleUrl = ref<string>("");
const parsingZhihuUrl = ref<boolean>(false);
const zhihuContents = ref<ZhihuContentItem[]>([]);
const loadingZhihuContents = ref<boolean>(false);
const syncingZhihuSource = ref<boolean>(false);
const zhihuContentTypeFilter = ref<string>("all");
const zhihuOnlyUnread = ref<boolean>(false);
const zhihuOnlyArchived = ref<boolean>(false);
const selectedZhihuIds = ref<string[]>([]);

// 右侧阅读器状态
const activeReaderItem = ref<ZhihuContentItem | null>(null);
const loadingReaderDetail = ref<boolean>(false);

const filteredZhihuCollections = computed(() => {
  if (zhihuCollectionFilter.value === "all") return zhihuCollections.value;
  return zhihuCollections.value.filter((c) => c.source_type === zhihuCollectionFilter.value);
});

const currentZhihuHeader = computed(() => {
  if (zhihuModule.value === "creators") {
    if (selectedZhihuCreator.value === "all") {
      return {
        title: "全部答主",
        badge: "",
        desc: `已获取 ${zhihuContents.value.length} 篇`,
        metaTitle: "",
        metaDetails: ""
      };
    }
    const found = zhihuCreators.value.find((c) => c.url_token === selectedZhihuCreator.value);
    return {
      title: found ? found.name : selectedZhihuCreator.value,
      badge: "",
      desc: `已获取 ${zhihuContents.value.length} 篇`,
      metaTitle: "作者资料",
      metaDetails: found
        ? `${found.headline || "暂无个人简介"} · 平台回答 ${found.answer_count} · 专栏文章 ${found.articles_count} · 已获取 ${found.cached_count || 0} 篇`
        : ""
    };
  }
  if (zhihuModule.value === "collections") {
    if (selectedZhihuCollection.value === "all") {
      return {
        title: "全部收藏夹",
        badge: "",
        desc: `已获取 ${zhihuContents.value.length} 篇`,
        metaTitle: "",
        metaDetails: ""
      };
    }
    const col = zhihuCollections.value.find((c) => c.collection_id === selectedZhihuCollection.value);
    const srcLabel = col?.source_type === "my" ? "自建收藏夹" : col?.source_type === "following" ? "关注收藏夹" : "外部收藏夹";
    return {
      title: col ? col.title : `收藏夹 #${selectedZhihuCollection.value}`,
      badge: "",
      desc: col ? `创建者：${col.creator_name || "知乎用户"} · 已获取 ${zhihuContents.value.length} 篇` : `已获取 ${zhihuContents.value.length} 篇`,
      metaTitle: "收藏夹信息",
      metaDetails: col ? `来源：${srcLabel} · 平台条目：${col.item_count} · 本地已获取：${col.cached_count || 0} 篇` : ""
    };
  }
  if (zhihuModule.value === "upvotes") {
    return {
      title: "我赞同的内容",
      badge: "",
      desc: `已获取 ${zhihuContents.value.length} 篇`,
      metaTitle: "",
      metaDetails: ""
    };
  }
  return {
    title: "链接解析",
    badge: "",
    desc: "",
    metaTitle: "",
    metaDetails: ""
  };
});

const localizedReaderHtml = computed(() => {
  let html = activeReaderItem.value?.content_html || "";
  if (!html) return "";
  // 移除 noscript 标签，防止备用占位冲突
  html = html.replace(/<noscript\b[^>]*>[\s\S]*?<\/noscript>/gi, "");
  // 提取并替换 data-actualsrc 或 data-original 懒加载原图
  html = html.replace(/<img\b([^>]*?)data-(?:actualsrc|original)=["']([^"']+)["']([^>]*)>/gi, (_m, pre, actualUrl, post) => {
    let combined = pre + post;
    const proxied = getProxyPicUrl(actualUrl);
    if (/src=["'][^"']*["']/i.test(combined)) {
      combined = combined.replace(/src=["'][^"']*["']/i, `src="${proxied}"`);
    } else {
      combined = ` src="${proxied}"` + combined;
    }
    return `<img ${combined}>`;
  });
  // 确保所有普通 src 图片通过本地反代走防盗链
  return html.replace(/<img([^>]*?)src=["'](https?:\/\/[^"']+)["']/gi, (_m, attrs, url) => {
    return `<img${attrs}src="${getProxyPicUrl(url)}"`;
  });
});

let zhihuAuthRetryTimer: ReturnType<typeof setTimeout> | null = null;
function retryZhihuVerification(token: string, attempts = 0) {
  if (zhihuAuthRetryTimer) clearTimeout(zhihuAuthRetryTimer);
  if (attempts >= 4 || !token) return;
  const delayMs = attempts === 0 ? 1200 : 15000;
  zhihuAuthRetryTimer = setTimeout(async () => {
    if (zhihuUser.value.url_token !== token) return;
    try {
      const state = await apiZhihuGetAuthStatus();
      if (zhihuUser.value.url_token !== token) return;
      zhihuUser.value = state;
      if (state.is_login && state.verification_state !== 'verified') retryZhihuVerification(token, attempts + 1);
    } catch (_) {
      retryZhihuVerification(token, attempts + 1);
    }
  }, delayMs);
}

async function initZhihuWorkspace(autoRefreshNetwork: boolean = true) {
  try {
    const authRes = await apiZhihuGetAuthStatus();
    if (zhihuUser.value.url_token !== authRes.url_token) {
      activeReaderItem.value = null;
      selectedZhihuCreator.value = "all";
      selectedZhihuCollection.value = "all";
      selectedZhihuIds.value = [];
      zhihuContents.value = [];
      upvoteBindToken.value = "";
    }
    zhihuUser.value = authRes;
    if (zhihuAuthRetryTimer) clearTimeout(zhihuAuthRetryTimer);
    if (authRes.is_login && authRes.verification_state !== "verified") retryZhihuVerification(authRes.url_token);
    if (authRes.url_token && !upvoteBindToken.value) {
      upvoteBindToken.value = authRes.url_token;
    }
    if (authRes.is_login && authRes.verification_state === "verified" && autoRefreshNetwork) {
      await apiZhihuInitAccountData().catch((e) => console.warn("知乎账号自动初始化通知:", e));
    }
  } catch (_) {}
  await loadZhihuAccounts();
  await loadZhihuSummary();
  await loadZhihuCreators();
  await loadZhihuCollections();
  await loadZhihuUpvotesMeta();
  await loadZhihuContents();
}

async function loadZhihuSummary() {
  try {
    zhihuSummary.value = await apiZhihuGetSummary();
  } catch (_) {}
}

async function loadZhihuCreators() {
  try {
    const res = await apiZhihuGetCreators("all");
    zhihuCreators.value = res.items || [];
  } catch (_) {}
}

async function loadZhihuCollections() {
  try {
    const res = await apiZhihuGetCollections("all");
    zhihuCollections.value = res.items || [];
  } catch (_) {}
}

async function loadZhihuUpvotesMeta() {
  try {
    const st = await apiZhihuGetUpvotesStatus();
    if (st.url_token) upvoteBindToken.value = st.url_token;
    latestUpvoteTime.value = st.latest_upvote_time || 0;
  } catch (_) {}
}

async function loadZhihuContents() {
  loadingZhihuContents.value = true;
  zhihuLoadError.value = "";
  try {
    let sourceType = "creator";
    let sourceId = "all";
    if (zhihuModule.value === "creators") {
      sourceType = "creator";
      sourceId = selectedZhihuCreator.value;
    } else if (zhihuModule.value === "collections") {
      sourceType = "collection";
      sourceId = selectedZhihuCollection.value;
    } else if (zhihuModule.value === "upvotes") {
      sourceType = "upvote";
      sourceId = "all";
    } else {
      sourceType = "single";
      sourceId = "all";
    }

    const res = await apiZhihuGetContents({
      source_type: sourceType,
      source_id: sourceId,
      content_type: zhihuContentTypeFilter.value,
      only_unread: zhihuOnlyUnread.value,
      only_archived: zhihuOnlyArchived.value,
      search: searchKeyword.value.trim()
    });
    zhihuContents.value = res.items || [];
    zhihuSummary.value = res.summary || zhihuSummary.value;
    selectedZhihuIds.value = [];
    showZhihuMoreMenu.value = false;
    if (!activeReaderItem.value && zhihuContents.value.length > 0) {
      activeReaderItem.value = zhihuContents.value[0];
    }
  } catch (e: any) {
    console.warn("加载知乎内容失败:", e);
    zhihuLoadError.value = e?.message || "加载知乎内容失败，请检查本地服务或网络状态";
  } finally {
    loadingZhihuContents.value = false;
  }
}

function clearZhihuFiltersAndSearch() {
  searchKeyword.value = "";
  zhihuContentTypeFilter.value = "all";
  zhihuOnlyUnread.value = false;
  zhihuOnlyArchived.value = false;
  loadZhihuContents();
}

function switchToZhihuModule(mod: "creators" | "collections" | "upvotes" | "single") {
  if (!confirmLeaveSettings()) return;
  activePlatform.value = "zhihu";
  zhihuModule.value = mod;
  if (!zhihuUser.value.is_login && zhihuAccounts.value.length === 0) {
    initZhihuWorkspace(false);
    return;
  }
  if (mod === "creators" && zhihuCreators.value.length === 0) loadZhihuCreators();
  if (mod === "collections" && zhihuCollections.value.length === 0) loadZhihuCollections();
  loadZhihuContents();
}

function switchToBiliMode(mode: "follow" | "fav" | "link") {
  if (!confirmLeaveSettings()) return;
  activePlatform.value = "bili";
  switchMode(mode);
}

async function handleAddZhihuCreator() {
  const txt = newCreatorInput.value.trim();
  if (!txt) return;
  addingCreator.value = true;
  try {
    const res = await apiZhihuAddCreator(txt);
    newCreatorInput.value = "";
    await loadZhihuCreators();
    selectedZhihuCreator.value = res.creator.url_token;
    await loadZhihuContents();
  } catch (e: any) {
    showToast("添加知乎答主失败: " + e.message);
  } finally {
    addingCreator.value = false;
  }
}

async function handleRemoveZhihuCreator(urlToken: string, name: string) {
  if (!confirm(`确定不再监听知乎答主「${name}」吗？`)) return;
  await apiZhihuRemoveCreator(urlToken);
  if (selectedZhihuCreator.value === urlToken) selectedZhihuCreator.value = "all";
  await loadZhihuCreators();
  await loadZhihuContents();
}

async function handleSyncAccountCreators() {
  syncingAccountCreators.value = true;
  try {
    const token = zhihuUser.value.url_token || upvoteBindToken.value.trim();
    const res = await apiZhihuSyncAccountCreators(token);
    await loadZhihuCreators();
    showToast(res.message);
  } catch (e: any) {
    showToast("导入关注答主失败: " + e.message);
  } finally {
    syncingAccountCreators.value = false;
  }
}

async function selectZhihuCreatorItem(token: string) {
  selectedZhihuCreator.value = token;
  await loadZhihuContents();
  if (token !== "all" && settingRefreshOnSelect.value) {
    handleSyncCurrentZhihuView(false, true);
  }
}

async function selectZhihuCollectionItem(cid: string) {
  selectedZhihuCollection.value = cid;
  await loadZhihuContents();
  if (cid !== "all" && settingRefreshOnSelect.value) {
    handleSyncCurrentZhihuView(false, true);
  }
}

async function handleAddZhihuCollection() {
  const txt = newCollectionInput.value.trim();
  if (!txt) return;
  addingCollection.value = true;
  try {
    const res = await apiZhihuAddCollection(txt);
    newCollectionInput.value = "";
    await loadZhihuCollections();
    selectedZhihuCollection.value = res.collection.collection_id;
    await loadZhihuContents();
  } catch (e: any) {
    showToast("添加知乎收藏夹失败: " + e.message);
  } finally {
    addingCollection.value = false;
  }
}

async function handleSyncAccountCollections() {
  syncingAccountCollections.value = true;
  try {
    const token = zhihuUser.value.url_token || upvoteBindToken.value.trim();
    const res = await apiZhihuSyncAccountCollections(token);
    zhihuCollections.value = res.items || [];
    showToast(res.message);
  } catch (e: any) {
    showToast("同步账号收藏夹失败: " + e.message);
  } finally {
    syncingAccountCollections.value = false;
  }
}

async function handleRemoveZhihuCollection(cid: string, title: string) {
  if (!confirm(`确定移除监听收藏夹「${title}」吗？`)) return;
  await apiZhihuRemoveCollection(cid);
  if (selectedZhihuCollection.value === cid) selectedZhihuCollection.value = "all";
  await loadZhihuCollections();
  await loadZhihuContents();
}

async function handleSyncCurrentZhihuView(deepScrape: boolean = false, silent: boolean = false) {
  syncingZhihuSource.value = true;
  try {
    if (zhihuModule.value === "creators") {
      if (selectedZhihuCreator.value === "all") {
        const res = await apiZhihuRunAutoCheckNow(settingAutoDownload.value, "creators");
        if (!silent) showToast(res.message);
      } else {
        const res = await apiZhihuSyncCreator(selectedZhihuCreator.value, deepScrape, deepScrape ? 5 : 1);
        if (!silent) showToast(res.message);
      }
      await loadZhihuCreators();
    } else if (zhihuModule.value === "collections") {
      if (selectedZhihuCollection.value === "all") {
        const res = await apiZhihuRunAutoCheckNow(settingAutoDownload.value, "collections");
        if (!silent) showToast(res.message);
      } else {
        const res = await apiZhihuSyncCollectionItems(selectedZhihuCollection.value, deepScrape, deepScrape ? 5 : 1);
        if (!silent) showToast(res.message);
      }
      await loadZhihuCollections();
    } else if (zhihuModule.value === "upvotes") {
      const res = await apiZhihuSyncUpvotes(upvoteBindToken.value.trim(), deepScrape, deepScrape ? 5 : 1);
      upvoteBindToken.value = res.url_token;
      if (!silent) showToast(res.message);
    }
    await loadZhihuContents();
    if (settingAutoDownload.value) {
      await pollTasks();
    }
  } catch (e: any) {
    if (!silent) showToast("知乎刷新失败: " + e.message);
  } finally {
    syncingZhihuSource.value = false;
  }
}

async function handleParseZhihuSingleUrl() {
  const u = zhihuSingleUrl.value.trim();
  if (!u) return;
  parsingZhihuUrl.value = true;
  try {
    const res = await apiZhihuParseUrl(u);
    zhihuSingleUrl.value = "";
    await loadZhihuContents();
    if (res.items && res.items.length > 0) {
      await openZhihuReader(res.items[0]);
    }
    showToast(res.message);
  } catch (e: any) {
    showToast("解析知乎链接失败: " + e.message);
  } finally {
    parsingZhihuUrl.value = false;
  }
}

async function openZhihuReader(item: ZhihuContentItem) {
  showRightPanel.value = true;
  rightPanelTab.value = "reader";
  activeReaderItem.value = item;
  loadingReaderDetail.value = true;
  try {
    const res = await apiZhihuGetContentDetail(item.content_id);
    if (res.item) {
      activeReaderItem.value = res.item;
      const idx = zhihuContents.value.findIndex((x) => x.content_id === item.content_id);
      if (idx >= 0) {
        zhihuContents.value[idx] = { ...zhihuContents.value[idx], ...res.item, is_read: 1 };
      }
      await loadZhihuSummary();
    }
  } catch (_) {
  } finally {
    loadingReaderDetail.value = false;
  }
}

async function handleArchiveZhihuSingle(item: ZhihuContentItem) {
  if (item.is_paid === 1 || item.is_restricted) {
    showToast("该条目属于知乎盐选/付费专栏内容，受版权保护，系统已严格禁止爬取与归档。");
    return;
  }
  try {
    const res = await apiZhihuArchiveItems([item.content_id]);
    item.download_status = "downloading";
    showRightPanel.value = true;
    rightPanelTab.value = "queue";
    await pollTasks();
    console.log(res.message);
  } catch (e: any) {
    showToast("提交知乎归档失败: " + e.message);
  }
}

async function handleBatchArchiveZhihu() {
  if (selectedZhihuIds.value.length === 0) {
    showToast("请先勾选需要归档的知乎帖子");
    return;
  }
  try {
    const res = await apiZhihuArchiveItems(selectedZhihuIds.value);
    showRightPanel.value = true;
    rightPanelTab.value = "queue";
    await pollTasks();
    await loadZhihuContents();
    showToast(res.message);
  } catch (e: any) {
    showToast("批量归档失败: " + e.message);
  }
}

async function handleMarkAllZhihuRead() {
  let sType: string | undefined = undefined;
  let sId: string | undefined = undefined;
  if (zhihuModule.value === "creators") {
    sType = "creator";
    if (selectedZhihuCreator.value !== "all") sId = selectedZhihuCreator.value;
  } else if (zhihuModule.value === "collections") {
    sType = "collection";
    if (selectedZhihuCollection.value !== "all") sId = selectedZhihuCollection.value;
  } else if (zhihuModule.value === "upvotes") {
    sType = "upvote";
  }
  await apiZhihuMarkRead({ source_type: sType, source_id: sId });
  await loadZhihuCreators();
  await loadZhihuCollections();
  await loadZhihuContents();
}

// 知乎扫码、真实浏览器过验证与 Cookie 登录逻辑
function stopZhihuTimers() {
  zhihuQrPollingActive = false;
  if (zhihuQrPollTimer) {
    clearTimeout(zhihuQrPollTimer);
    zhihuQrPollTimer = null;
  }
  if (zhihuBrowserPollTimer) {
    clearInterval(zhihuBrowserPollTimer);
    zhihuBrowserPollTimer = null;
  }
}

async function changeZhihuLoginTab(tab: "qrcode" | "browser" | "cookie") {
  ++zhihuLoginGeneration;
  stopZhihuTimers();
  zhihuBrowserLoggingIn.value = false;
  zhihuLoginTab.value = tab;
  try {
    await apiZhihuCancelLogin();
    if (tab === "qrcode" && showZhihuLoginModal.value) await startZhihuQrFlow();
  } catch (e: any) {
    showToast("切换登录方式失败: " + e.message);
  }
}

async function openZhihuLoginModal(tab: "qrcode" | "browser" | "cookie" = "qrcode") {
  showZhihuLoginModal.value = true;
  zhihuLoginTab.value = tab;
  rememberAndFocusModal('.modal-card[role="dialog"]');
  if (tab === "qrcode") {
    await startZhihuQrFlow();
  }
}

async function pollZhihuQrLoop() {
  if (!zhihuQrPollingActive || !showZhihuLoginModal.value || zhihuLoginTab.value !== "qrcode") {
    return;
  }
  const generation = zhihuLoginGeneration;
  try {
    const poll = await apiZhihuPollQrCode(zhihuQrKey.value);
    if (!zhihuQrPollingActive || generation !== zhihuLoginGeneration) return;

    zhihuQrStatus.value = poll.message;
    if (poll.status === "success") {
      stopZhihuTimers();
      showZhihuLoginModal.value = false;
      await initZhihuWorkspace();
      return;
    } else if (poll.status === "rotated" && poll.qrcode_key && poll.qrcode_img) {
      // 知乎后台 status=5 自动轮换了新二维码，无缝替换图片与 key，不中断轮询
      zhihuQrKey.value = poll.qrcode_key;
      zhihuQrImg.value = poll.qrcode_img;
    } else if (poll.status === "blocked") {
      // 当前网络触发知乎 40352 风控，停止轮询并引导点击「浏览器窗口登录」
      zhihuQrBlocked.value = true;
      stopZhihuTimers();
      return;
    } else if (poll.status === "expired" || poll.status === "error") {
      stopZhihuTimers();
      return;
    }
  } catch (_) {}

  if (zhihuQrPollingActive && showZhihuLoginModal.value) {
    zhihuQrPollTimer = setTimeout(pollZhihuQrLoop, 1600);
  }
}

async function startZhihuQrFlow() {
  const generation = ++zhihuLoginGeneration;
  stopZhihuTimers();
  zhihuQrBlocked.value = false;
  zhihuQrStatus.value = "正在向知乎申请登录二维码...";
  zhihuQrImg.value = "";
  try {
    await apiZhihuCancelLogin();
    if (generation !== zhihuLoginGeneration) return;
    const res = await apiZhihuGenerateQrCode();
    if (generation !== zhihuLoginGeneration || !showZhihuLoginModal.value) return;
    if (res.success && res.qrcode_img && res.qrcode_key) {
      zhihuQrImg.value = res.qrcode_img;
      zhihuQrKey.value = res.qrcode_key;
      zhihuQrStatus.value = "请打开手机【知乎 App】扫码登录";
      zhihuQrPollingActive = true;
      zhihuQrPollTimer = setTimeout(pollZhihuQrLoop, 1500);
    } else {
      zhihuQrBlocked.value = true;
      zhihuQrStatus.value = res.message || "二维码受限，请点击下方「浏览器窗口扫码/过验证」或切换「Cookie 导入」";
    }
  } catch (e: any) {
    zhihuQrBlocked.value = true;
    zhihuQrStatus.value = "申请二维码失败，请使用「浏览器窗口登录」或「Cookie 导入」";
  }
}

async function handleStartZhihuBrowserLogin() {
  const generation = ++zhihuLoginGeneration;
  stopZhihuTimers();
  zhihuLoginTab.value = "browser";
  zhihuBrowserLoggingIn.value = true;
  zhihuQrStatus.value = "正在启动本地 Edge/Chrome 浏览器窗口，请在弹出的知乎页面中完成验证或扫码...";
  try {
    await apiZhihuCancelLogin();
    if (generation !== zhihuLoginGeneration) return;
    const res = await apiZhihuStartBrowserLogin();
    if (generation !== zhihuLoginGeneration || !showZhihuLoginModal.value) return;
    zhihuQrStatus.value = res.message || "请在弹出的浏览器窗口中完成验证或扫码登录...";
    zhihuBrowserPollTimer = setInterval(async () => {
      try {
        const st = await apiZhihuPollBrowserLogin();
        if (generation !== zhihuLoginGeneration || !showZhihuLoginModal.value) return;
        if (st.message) zhihuQrStatus.value = st.message;
        if (st.status === "success") {
          zhihuBrowserLoggingIn.value = false;
          stopZhihuTimers();
          showZhihuLoginModal.value = false;
          await initZhihuWorkspace();
        } else if (st.status === "failed") {
          zhihuBrowserLoggingIn.value = false;
          stopZhihuTimers();
        }
      } catch (_) {}
    }, 1500);
  } catch (e: any) {
    zhihuBrowserLoggingIn.value = false;
    zhihuQrStatus.value = "启动浏览器窗口失败: " + (e.message || e);
  }
}

function closeZhihuLoginModal() {
  ++zhihuLoginGeneration;
  void apiZhihuCancelLogin().catch((e) => showToast("取消登录失败: " + e.message));
  showZhihuLoginModal.value = false;
  zhihuBrowserLoggingIn.value = false;
  stopZhihuTimers();
}

async function handleImportZhihuCookie() {
  if (!zhihuCookieInput.value.trim()) {
    zhihuCookieError.value = "请粘贴知乎 Cookie（需包含 z_c0）";
    return;
  }
  zhihuCookieLoading.value = true;
  zhihuCookieError.value = "";
  try {
    const res = await apiZhihuImportCookie(zhihuCookieInput.value.trim());
    zhihuCookieInput.value = "";
    closeZhihuLoginModal();
    await initZhihuWorkspace();
    showToast(res.message);
  } catch (e: any) {
    zhihuCookieError.value = e.message || "导入失败";
  } finally {
    zhihuCookieLoading.value = false;
  }
}

async function handleZhihuLogout() {
  if (!confirm("确定退出当前知乎账号吗？")) return;
  try {
    await apiZhihuLogout();
    await initZhihuWorkspace(false);
  } catch (e: any) {
    showToast("退出知乎失败: " + e.message);
  }
}

async function loadSystemLogs() {
  loadingSystemLogs.value = true;
  try {
    const res = await apiGetSystemLogs(180);
    systemLogFile.value = res.log_file || "";
    systemLogLines.value = res.lines || [];
  } catch (_) {
  } finally {
    loadingSystemLogs.value = false;
  }
}

function switchToTasksPlatform(sub: "all" | "running" | "success" | "failed" | "logs" = "all") {
  if (!confirmLeaveSettings()) return;
  activePlatform.value = "tasks";
  taskViewSubTab.value = sub;
  if (sub === "logs") {
    loadSystemLogs();
  } else {
    drawerTab.value = sub;
  }
}

function formatZhihuTime(ts?: number) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
}


// ==================== 桌面窗口原生控制 (Tauri + Web 降级) ====================
async function handleMinimizeWindow() {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().minimize();
  } catch {
    try {
      await invoke("win_minimize");
    } catch (e) {
      console.warn("Minimize window not supported in browser", e);
    }
  }
}

async function handleToggleMaximizeWindow() {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const win = getCurrentWindow();
    await win.toggleMaximize();
    setTimeout(async () => {
      isWindowMaximized.value = await win.isMaximized();
    }, 120);
  } catch {
    try {
      await invoke("win_toggle_maximize");
    } catch (e) {
      console.warn("Maximize window not supported in browser", e);
    }
  }
}

async function handleCloseWindow() {
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    await getCurrentWindow().close();
  } catch {
    try {
      await invoke("win_close");
    } catch (e) {
      window.close();
    }
  }
}



</script>

<template>
  <div class="app-layout" :data-theme="themeMode" :class="{ 'is-maximized': isWindowMaximized }">
    <!-- 顶部导航栏 -->
    <header class="navbar" data-tauri-drag-region>
      <!-- 左侧：品牌标识 -->
      <div class="brand" data-tauri-drag-region>
        <img src="/app-icon.png" alt="GatherBox" class="brand-logo-img" data-tauri-drag-region />
        <div class="brand-info" data-tauri-drag-region>
          <span class="brand-app-name" data-tauri-drag-region>GatherBox</span>
        </div>
      </div>

      <!-- 全局搜索区域：随当前工作区动态变化，在设置与账号页自动隐藏 -->
      <div
        v-if="activePlatform === 'zhihu' || activePlatform === 'bili' || activePlatform === 'tasks'"
        class="navbar-search"
      >
        <AppIcon name="search" :size="14" class="search-icon" />
        <input
          v-model="searchKeyword"
          type="text"
          class="nav-search-input"
          :placeholder="
            activePlatform === 'zhihu'
              ? '搜索知乎标题、摘要或答主...'
              : activePlatform === 'bili'
              ? '搜索B站视频标题、UP主或 BV号...'
              : '搜索任务标题、作者或 BV号...'
          "
          @keyup.enter="handleSearch"
        />
        <button
          v-if="searchKeyword"
          type="button"
          class="btn-clear-search"
          title="清空搜索"
          @click="clearGlobalSearch"
        >
          <AppIcon name="close" :size="11" />
        </button>
      </div>
      <div v-else style="flex: 1;" data-tauri-drag-region></div>

      <!-- 导航操作与 Windows 标准右侧窗口控件 -->
      <div class="navbar-actions">
        <!-- 后端服务与后台巡检合并状态胶囊 -->
        <button
          type="button"
          class="server-status"
          :class="{ offline: !serverOnline }"
          @click="serverOnline ? openSettings('autocheck') : initServerConnection()"
          :title="
            !serverOnline
              ? '本地后台服务未连接，点击尝试重新连接'
              : settingAutoCheck
              ? `服务在线 · 定时刷新运行中 (每 ${settingCheckInterval} 分钟)`
              : '服务在线 · 点击配置后台自动刷新'
          "
        >
          <span class="dot"></span>
          <span>{{ serverOnline ? (autoCheckStatus.is_running ? "后台刷新中" : settingAutoCheck ? "已连接 · 定时开" : "服务已连接") : "服务离线 (点击重连)" }}</span>
        </button>

        <!-- 日夜模式单按钮切换 -->
        <button
          type="button"
          class="btn-tool btn-theme-toggle"
          @click="toggleTheme"
          :title="themeMode === 'dark' ? '当前深色模式，点击切换至浅色模式' : '当前浅色模式，点击切换至深色模式'"
        >
          <AppIcon :name="themeMode === 'dark' ? 'light_mode' : 'dark_mode'" :size="14" />
          <span class="nav-btn-label">{{ themeMode === 'dark' ? '浅色' : '深色' }}</span>
        </button>

        <!-- 右侧阅读器与近期进度抽屉开关 -->
        <button
          v-if="activePlatform === 'zhihu' || activePlatform === 'bili'"
          type="button"
          class="btn-tool"
          :class="{ active: showRightPanel }"
          @click="showRightPanel = !showRightPanel"
          title="展开/收起右侧阅读器与近期进度侧栏"
        >
          <AppIcon name="book" :size="14" />
          <span class="nav-btn-label">{{ showRightPanel ? "收起侧栏" : "阅读与进度" }}</span>
          <span v-if="activeDownloadCount > 0" class="badge-pulse">{{ activeDownloadCount }}</span>
        </button>

        <!-- Windows 桌面标准右上角窗口控制按钮 -->
        <div class="win-window-controls" title="窗口控制">
          <button type="button" class="win-ctrl-btn min" @click.stop="handleMinimizeWindow" title="最小化" aria-label="最小化">─</button>
          <button type="button" class="win-ctrl-btn max" @click.stop="handleToggleMaximizeWindow" title="最大化/还原" aria-label="最大化或还原">□</button>
          <button type="button" class="win-ctrl-btn close" @click.stop="handleCloseWindow" title="关闭窗口" aria-label="关闭窗口">✕</button>
        </div>
      </div>
    </header>

    <div class="main-body">
      <!-- ==================== 第 1 栏：最左侧可折叠一级 Tab（平台 + 任务队列 + 左下角设置） ==================== -->
      <aside class="primary-platform-rail" :class="{ collapsed: primarySidebarCollapsed }">
        <div class="rail-header-row">
          <span v-if="!primarySidebarCollapsed" class="rail-section-label">工作台导航</span>
          <button
            class="btn-tool btn-collapse-rail"
            @click="primarySidebarCollapsed = !primarySidebarCollapsed"
            :title="primarySidebarCollapsed ? '展开左侧平台栏' : '收起左侧平台栏'"
          >
            <AppIcon :name="primarySidebarCollapsed ? 'menu' : 'chevron_left'" :size="13" />
          </button>
        </div>
        <div class="rail-scroll">

          <!-- 一级 Tab 1：知乎 -->
          <div class="platform-group-card" :class="{ active: activePlatform === 'zhihu' }">
            <button type="button" class="platform-group-header" @click="if (confirmLeaveSettings()) { zhihuGroupExpanded = !zhihuGroupExpanded; activePlatform = 'zhihu'; }">
              <div class="platform-title-wrap">
                <span class="platform-logo zhihu"><img src="/zhihu.png" class="platform-logo-img" alt="知乎" /></span>
                <span v-if="!primarySidebarCollapsed" class="platform-name">知乎 (Zhihu)</span>
              </div>
              <div v-if="!primarySidebarCollapsed" class="platform-meta">
                <span v-if="zhihuSummary.total_unread > 0" class="platform-unread-pill zhihu">{{ zhihuSummary.total_unread }}</span>
                <span class="platform-arrow">{{ zhihuGroupExpanded ? "▼" : "▶" }}</span>
              </div>
            </button>

            <div v-if="zhihuGroupExpanded" class="platform-sub-list">
              <button
                class="platform-sub-btn"
                :class="{ active: activePlatform === 'zhihu' && zhihuModule === 'creators' }"
                @click="switchToZhihuModule('creators')"
                title="知乎关注的答主回答与文章更新"
              >
                <span class="sub-icon"><AppIcon name="person" :size="14" /></span>
                <span v-if="!primarySidebarCollapsed" class="sub-text">关注答主</span>
                <span v-if="!primarySidebarCollapsed && zhihuSummary.creator_unread > 0" class="sub-badge zhihu">{{ zhihuSummary.creator_unread }}</span>
              </button>

              <button
                class="platform-sub-btn"
                :class="{ active: activePlatform === 'zhihu' && zhihuModule === 'collections' }"
                @click="switchToZhihuModule('collections')"
                title="知乎收藏夹（自建/关注/外部）订阅"
              >
                <span class="sub-icon"><AppIcon name="folder" :size="14" /></span>
                <span v-if="!primarySidebarCollapsed" class="sub-text">收藏夹</span>
                <span v-if="!primarySidebarCollapsed && zhihuSummary.collection_unread > 0" class="sub-badge zhihu">{{ zhihuSummary.collection_unread }}</span>
              </button>

              <button
                class="platform-sub-btn"
                :class="{ active: activePlatform === 'zhihu' && zhihuModule === 'upvotes' }"
                @click="switchToZhihuModule('upvotes')"
                title="自动同步我赞同的知乎回答与专栏文章"
              >
                <span class="sub-icon"><AppIcon name="thumb_up" :size="14" /></span>
                <span v-if="!primarySidebarCollapsed" class="sub-text">赞同动态</span>
                <span v-if="!primarySidebarCollapsed && zhihuSummary.upvote_unread > 0" class="sub-badge emerald">{{ zhihuSummary.upvote_unread }}</span>
              </button>

              <button
                class="platform-sub-btn"
                :class="{ active: activePlatform === 'zhihu' && zhihuModule === 'single' }"
                @click="switchToZhihuModule('single')"
                title="单篇知乎回答、专栏文章或问题链接解析"
              >
                <span class="sub-icon"><AppIcon name="link" :size="14" /></span>
                <span v-if="!primarySidebarCollapsed" class="sub-text">链接解析</span>
              </button>
            </div>
          </div>

          <!-- 一级 Tab 2：哔哩哔哩 -->
          <div class="platform-group-card" :class="{ active: activePlatform === 'bili' }">
            <button type="button" class="platform-group-header" @click="if (confirmLeaveSettings()) { biliGroupExpanded = !biliGroupExpanded; activePlatform = 'bili'; }">
              <div class="platform-title-wrap">
                <span class="platform-logo bili"><img src="/bilibili.png" class="platform-logo-img" alt="哔哩哔哩" /></span>
                <span v-if="!primarySidebarCollapsed" class="platform-name">哔哩哔哩 (Bili)</span>
              </div>
              <div v-if="!primarySidebarCollapsed" class="platform-meta">
                <span v-if="unreadVideosCount > 0" class="platform-unread-pill bili">{{ unreadVideosCount }}</span>
                <span class="platform-arrow">{{ biliGroupExpanded ? "▼" : "▶" }}</span>
              </div>
            </button>

            <div v-if="biliGroupExpanded" class="platform-sub-list">
              <button
                class="platform-sub-btn bili-mode"
                :class="{ active: activePlatform === 'bili' && activeMode === 'follow' }"
                @click="switchToBiliMode('follow')"
                title="B站关注UP主投稿更新"
              >
                <span class="sub-icon"><AppIcon name="person" :size="14" /></span>
                <span v-if="!primarySidebarCollapsed" class="sub-text">关注UP主</span>
                <span v-if="!primarySidebarCollapsed && unreadVideosCount > 0" class="sub-badge bili">{{ unreadVideosCount }}</span>
              </button>

              <button
                class="platform-sub-btn bili-mode"
                :class="{ active: activePlatform === 'bili' && activeMode === 'fav' }"
                @click="switchToBiliMode('fav')"
                title="B站收藏夹同步"
              >
                <span class="sub-icon"><AppIcon name="folder" :size="14" /></span>
                <span v-if="!primarySidebarCollapsed" class="sub-text">收藏夹</span>
              </button>

              <button
                class="platform-sub-btn bili-mode"
                :class="{ active: activePlatform === 'bili' && activeMode === 'link' }"
                @click="switchToBiliMode('link')"
                title="B站单视频/分P/合集链接解析"
              >
                <span class="sub-icon"><AppIcon name="link" :size="14" /></span>
                <span v-if="!primarySidebarCollapsed" class="sub-text">链接解析</span>
              </button>
            </div>
          </div>

          <!-- 一级 Tab 3：任务队列与运行日志 -->
          <div class="platform-group-card" :class="{ active: activePlatform === 'tasks' }">
            <button
              class="platform-group-header"
              @click="switchToTasksPlatform('all')"
              title="跨平台下载/归档任务队列与运行日志"
            >
              <div class="platform-title-wrap">
                <span class="platform-logo" style="background: var(--sys-accent-green); color: #fff; display: flex; align-items: center; justify-content: center;"><AppIcon name="inbox" :size="14" /></span>
                <span v-if="!primarySidebarCollapsed" class="platform-name">任务队列</span>
              </div>
              <div v-if="!primarySidebarCollapsed" class="platform-meta">
                <span v-if="activeDownloadCount > 0" class="badge-pulse">{{ activeDownloadCount }}</span>
                <span v-else-if="allTasks.length > 0" class="platform-unread-pill" style="background: var(--sys-tint-green); color: var(--sys-accent-green);">{{ allTasks.length }}</span>
              </div>
            </button>
          </div>

          <!-- 一级 Tab 4：账号中心 -->
          <div class="platform-group-card" :class="{ active: activePlatform === 'accounts' }">
            <button
              class="platform-group-header"
              @click="switchToAccountCenter('all')"
              title="统一管理多平台账号登录、启用状态与定时刷新开关"
            >
              <div class="platform-title-wrap">
                <span class="platform-logo" style="background: var(--sys-accent-indigo); color: #fff; display: flex; align-items: center; justify-content: center;"><AppIcon name="group" :size="14" /></span>
                <span v-if="!primarySidebarCollapsed" class="platform-name">账号中心</span>
              </div>
              <div v-if="!primarySidebarCollapsed" class="platform-meta">
                <span class="platform-unread-pill" style="background: var(--sys-tint-indigo); color: var(--sys-accent-indigo);">
                  {{ enabledZhihuAccounts.length + enabledBiliAccounts.length }}
                </span>
              </div>
            </button>
          </div>
        </div>

        <!-- 最左栏左下角固定：系统设置中心与快捷工具入口（语义化 button 支持 Tab 聚焦） -->
        <div class="rail-footer">
          <button
            type="button"
            class="rail-account-box"
            :class="{ active: activePlatform === 'settings' }"
            @click="openSettings('download')"
            title="打开全局系统设置中心 (全屏)"
          >
            <span class="platform-logo" style="background: var(--btn-sec-bg); color: var(--text-main); display: flex; align-items: center; justify-content: center;"><AppIcon name="settings" :size="14" /></span>
            <div v-if="!primarySidebarCollapsed" class="rail-acc-info">
              <div class="rail-acc-name">系统设置</div>
              <div class="rail-acc-sub">首选项与参数</div>
            </div>
          </button>
        </div>
      </aside>

      <!-- ==================== 第 2 栏：次左侧二级功能与简洁账号切换条 ==================== -->
      <aside class="sidebar">
        <!-- 分支 A：知乎二级源管理面板（顶部仅保留极简已启用账号切换器） -->
        <template v-if="activePlatform === 'zhihu'">
          <div class="platform-inline-auth-card zhihu" style="position: relative;">
            <div class="inline-auth-top" @click="toggleZhihuAccountPopover">
              <img v-if="zhihuUser.is_login && zhihuUser.face" :src="getProxyPicUrl(zhihuUser.face)" class="inline-auth-avatar" />
              <div v-else class="inline-auth-avatar-placeholder zhihu"><AppIcon v-if="zhihuUser.is_login" name="check" :size="14" /><span v-else>知</span></div>
              <div class="inline-auth-info">
                <div class="inline-auth-name">
                  <span class="truncate">{{ zhihuUser.is_login ? zhihuUser.uname : "未登录" }}</span>
                  <span v-if="zhihuUser.is_login" class="inline-auth-status online">{{ zhihuUser.verification_state === "unavailable" ? "暂无法验证" : zhihuUser.verification_state === "pending" ? "待验证" : "已启用" }}</span>
                </div>
                <div class="inline-auth-sub">
                  {{ enabledZhihuAccounts.length > 0 ? `已启用 ${enabledZhihuAccounts.length} 个账号 · 切换 ▾` : "点击右侧管理登录账号" }}
                </div>
              </div>
              <button class="btn-micro" @click.stop="switchToAccountCenter('zhihu')" title="前往账号中心管理知乎账号">
                <AppIcon name="settings" :size="11" /><span>管理</span>
              </button>
            </div>

            <!-- 知乎已启用账号切换下拉层 -->
            <div v-if="showZhihuAccountPopover" class="account-popover" style="top: 100%; bottom: auto; margin-top: 6px; z-index: 60;">
              <div class="popover-header">
                <span style="display:inline-flex;align-items:center;gap:4px;"><AppIcon name="group" :size="13" />切换已启用的知乎账号</span>
                <button class="btn-close-sm" @click="showZhihuAccountPopover = false"><AppIcon name="close" :size="11" /></button>
              </div>
              <div class="account-list">
                <div v-if="enabledZhihuAccounts.length === 0" style="padding: 10px; font-size: 11px; color: var(--text-muted); text-align: center;">
                  暂无已启用知乎账号
                </div>
                <div
                  v-for="acc in enabledZhihuAccounts"
                  :key="acc.url_token"
                  class="account-row"
                  :class="{ active: acc.is_current === 1 }"
                  @click="switchZhihuAccount(acc.url_token)"
                >
                  <img :src="acc.face ? getProxyPicUrl(acc.face) : 'https://static.hdslb.com/images/member/noface.gif'" class="acc-face" />
                  <div class="acc-info">
                    <div class="acc-name-line">
                      <span class="acc-name">{{ acc.uname }}</span>
                      <span v-if="acc.is_current === 1" class="tag-current">当前</span>
                    </div>
                    <span class="acc-uid">{{ acc.url_token }}</span>
                  </div>
                </div>
              </div>
              <div style="padding: 6px 8px; border-top: 1px solid var(--border-color); display: flex; justify-content: space-between;">
                <button class="btn-micro primary" style="width: 100%; justify-content: center;" @click="showZhihuAccountPopover = false; switchToAccountCenter('zhihu')">
                  <AppIcon name="person" :size="11" /><span>打开账号中心管理登录</span>
                </button>
              </div>
            </div>
          </div>

          <!-- A1. 关注答主列表 -->
          <div v-if="zhihuModule === 'creators'" class="zhihu-secondary-panel">
            <div class="zhihu-sec-header">
              <div class="zhihu-sec-title-row">
                <span>关注答主 ({{ zhihuCreators.length }})</span>
                <div style="display: flex; gap: 4px; flex-shrink: 0;">
                  <button type="button" class="btn-micro primary" :disabled="syncingAccountCreators" @click="handleSyncAccountCreators" title="从当前登录知乎账号同步我关注的答主列表">
                    {{ syncingAccountCreators ? "同步中" : "同步答主" }}
                  </button>
                </div>
              </div>
              <div class="zhihu-add-row">
                <input
                  v-model="newCreatorInput"
                  type="text"
                  placeholder="知乎主页链接或 url_token..."
                  class="zhihu-sec-input"
                  @keyup.enter="handleAddZhihuCreator"
                />
                <button class="btn-micro primary" :disabled="addingCreator" @click="handleAddZhihuCreator">
                  {{ addingCreator ? "..." : "+ 添加" }}
                </button>
              </div>
            </div>

            <div class="zhihu-sec-list">
              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: selectedZhihuCreator === 'all' }"
                @click="selectedZhihuCreator = 'all'; loadZhihuContents()"
              >
                <div class="zhihu-src-avatar"><AppIcon name="group" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">全部关注答主</div>
                  <div class="zhihu-src-sub">汇总查看 {{ zhihuCreators.length }} 位答主更新</div>
                </div>
                <span v-if="zhihuSummary.creator_unread > 0" class="sub-badge zhihu">{{ zhihuSummary.creator_unread }}</span>
              </div>

              <div
                v-for="cr in zhihuCreators"
                :key="cr.url_token"
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: selectedZhihuCreator === cr.url_token }"
                @click="selectZhihuCreatorItem(cr.url_token)"
              >
                <img
                  v-if="cr.avatar_url"
                  :src="getProxyPicUrl(cr.avatar_url)"
                  class="zhihu-src-avatar-img"
                />
                <div v-else class="zhihu-src-avatar">{{ (cr.name || "知").slice(0, 1) }}</div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">{{ cr.name }}</div>
                  <div class="zhihu-src-sub">回答 {{ cr.answer_count }} · 文章 {{ cr.articles_count }} · 已获取 {{ cr.cached_count || 0 }}</div>
                </div>
                <span v-if="cr.unread_count" class="sub-badge zhihu">{{ cr.unread_count }}</span>
                <button class="btn-src-del" title="取消关注" @click.stop="handleRemoveZhihuCreator(cr.url_token, cr.name)"><AppIcon name="close" :size="11" /></button>
              </div>
            </div>
          </div>

          <!-- A2. 收藏夹列表 -->
          <div v-else-if="zhihuModule === 'collections'" class="zhihu-secondary-panel">
            <div class="zhihu-sec-header">
              <div class="zhihu-sec-title-row">
                <span>收藏夹 ({{ zhihuCollections.length }})</span>
                <button type="button" class="btn-micro primary" :disabled="syncingAccountCollections" @click="handleSyncAccountCollections" title="从当前登录知乎账号同步自建与关注的收藏夹">
                  <AppIcon name="refresh" :size="11" /> <span>{{ syncingAccountCollections ? "同步中" : "同步收藏夹" }}</span>
                </button>
              </div>
              <div class="zhihu-add-row">
                <input
                  v-model="newCollectionInput"
                  type="text"
                  placeholder="收藏夹链接或 ID..."
                  class="zhihu-sec-input"
                  @keyup.enter="handleAddZhihuCollection"
                />
                <button class="btn-micro" :disabled="addingCollection" @click="handleAddZhihuCollection">
                  {{ addingCollection ? "..." : "+ 添加" }}
                </button>
              </div>
              <div class="zhihu-sub-pills">
                <button :class="{ active: zhihuCollectionFilter === 'all' }" @click="zhihuCollectionFilter = 'all'">全部</button>
                <button :class="{ active: zhihuCollectionFilter === 'my' }" @click="zhihuCollectionFilter = 'my'">自建</button>
                <button :class="{ active: zhihuCollectionFilter === 'following' }" @click="zhihuCollectionFilter = 'following'">关注</button>
                <button :class="{ active: zhihuCollectionFilter === 'custom' }" @click="zhihuCollectionFilter = 'custom'">外部</button>
              </div>
            </div>

            <div class="zhihu-sec-list">
              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: selectedZhihuCollection === 'all' }"
                @click="selectedZhihuCollection = 'all'; loadZhihuContents()"
              >
                <div class="zhihu-src-avatar"><AppIcon name="book" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">全部收藏夹</div>
                  <div class="zhihu-src-sub">共 {{ zhihuCollections.length }} 个收藏夹内容汇总</div>
                </div>
                <span v-if="zhihuSummary.collection_unread > 0" class="sub-badge zhihu">{{ zhihuSummary.collection_unread }}</span>
              </div>

              <div
                v-for="col in filteredZhihuCollections"
                :key="col.collection_id"
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: selectedZhihuCollection === col.collection_id }"
                @click="selectZhihuCollectionItem(col.collection_id)"
              >
                <div class="zhihu-src-avatar"><AppIcon :name="col.source_type === 'my' ? 'folder' : 'star'" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name" :title="col.title">{{ col.title }}</div>
                  <div class="zhihu-src-sub">{{ col.creator_name || '知乎' }} · {{ col.item_count }} 条 · 已获取 {{ col.cached_count || 0 }}</div>
                </div>
                <span v-if="col.unread_count" class="sub-badge zhihu">{{ col.unread_count }}</span>
                <button class="btn-src-del" title="移除收藏夹" @click.stop="handleRemoveZhihuCollection(col.collection_id, col.title)"><AppIcon name="close" :size="11" /></button>
              </div>
            </div>
          </div>

          <!-- A3. 赞同动态 -->
          <div v-else-if="zhihuModule === 'upvotes'" class="zhihu-secondary-panel">
            <div class="zhihu-sec-header">
              <div class="zhihu-sec-title-row">
                <span style="display: inline-flex; align-items: center; gap: 4px;"><AppIcon name="thumb_up" :size="14" /> 赞同动态</span>
              </div>
              <p style="font-size: 11px; color: var(--text-muted); margin: 4px 0 8px; line-height: 1.4;">
                绑定知乎主页 <code>url_token</code>，自动同步你在知乎赞同的回答与文章：
              </p>
              <div class="zhihu-add-row">
                <input
                  v-model="upvoteBindToken"
                  type="text"
                  placeholder="输入知乎 url_token..."
                  class="zhihu-sec-input"
                  @keyup.enter="handleSyncCurrentZhihuView(false)"
                />
                <button class="btn-micro primary" :disabled="syncingZhihuSource" @click="handleSyncCurrentZhihuView(false)">
                  {{ syncingZhihuSource ? "同步中" : "拉取" }}
                </button>
              </div>
            </div>

            <div class="zhihu-sec-list">
              <div class="zhihu-source-item" role="button" tabindex="0" :class="{ active: zhihuContentTypeFilter === 'all' }" @click="zhihuContentTypeFilter = 'all'; loadZhihuContents()">
                <div class="zhihu-src-avatar"><AppIcon name="thumb_up" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">全部赞同内容</div>
                  <div class="zhihu-src-sub">已获取 {{ zhihuSummary.upvote_total }} 篇赞同内容</div>
                </div>
                <span v-if="zhihuSummary.upvote_unread > 0" class="sub-badge emerald">{{ zhihuSummary.upvote_unread }}</span>
              </div>

              <div class="zhihu-source-item" role="button" tabindex="0" :class="{ active: zhihuContentTypeFilter === 'answer' }" @click="zhihuContentTypeFilter = 'answer'; loadZhihuContents()">
                <div class="zhihu-src-avatar"><AppIcon name="file" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">赞同的回答</div>
                  <div class="zhihu-src-sub">仅看知乎问答回答</div>
                </div>
              </div>

              <div class="zhihu-source-item" role="button" tabindex="0" :class="{ active: zhihuContentTypeFilter === 'article' }" @click="zhihuContentTypeFilter = 'article'; loadZhihuContents()">
                <div class="zhihu-src-avatar"><AppIcon name="book" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">赞同的专栏文章</div>
                  <div class="zhihu-src-sub">仅看知乎专栏文章</div>
                </div>
              </div>
            </div>
          </div>

          <!-- A4. 链接解析 -->
          <div v-else class="zhihu-secondary-panel">
            <div class="zhihu-sec-header">
              <div class="zhihu-sec-title-row">
                <span>链接解析</span>
              </div>
            </div>
            <div class="zhihu-sec-list">
              <div class="zhihu-source-item active">
                <div class="zhihu-src-avatar"><AppIcon name="link" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">解析记录</div>
                  <div class="zhihu-src-sub">已获取 {{ zhihuContents.length }} 篇</div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 分支 B：哔哩哔哩二级侧边栏（顶部仅保留极简已启用账号切换器） -->
        <template v-else-if="activePlatform === 'bili'">
          <div class="platform-inline-auth-card bili" style="position: relative;">
            <div class="inline-auth-top" @click="toggleAccountPopover">
              <img
                v-if="user.is_login"
                :src="user.face || 'https://static.hdslb.com/images/member/noface.gif'"
                class="inline-auth-avatar"
              />
              <div v-else class="inline-auth-avatar-placeholder bili">B</div>
              <div class="inline-auth-info">
                <div class="inline-auth-name">
                  <span class="truncate">{{ user.is_login ? user.uname : "未登录" }}</span>
                  <span v-if="user.is_login && user.vip_type" class="vip-tag">大会员</span>
                  <span v-else-if="user.is_login" class="inline-auth-status online">已启用</span>
                </div>
                <div class="inline-auth-sub">
                  {{ enabledBiliAccounts.length > 0 ? `已启用 ${enabledBiliAccounts.length} 个账号 · 切换 ▾` : "点击右侧管理登录账号" }}
                </div>
              </div>
              <button class="btn-micro" @click.stop="switchToAccountCenter('bili')" title="前往账号中心管理B站账号">
                <AppIcon name="settings" :size="11" /><span>管理</span>
              </button>
            </div>

            <!-- 多账号切换弹出层（仅展示已启用账号） -->
            <div v-if="showAccountPopover" class="account-popover" style="top: 100%; bottom: auto; margin-top: 6px; z-index: 60;">
              <div class="popover-header">
                <span style="display:inline-flex;align-items:center;gap:4px;"><AppIcon name="group" :size="13" />切换已启用的 B站 账号</span>
                <button class="btn-close-sm" @click="showAccountPopover = false"><AppIcon name="close" :size="11" /></button>
              </div>
              <div class="account-list">
                <div v-if="enabledBiliAccounts.length === 0" style="padding: 10px; font-size: 11px; color: var(--text-muted); text-align: center;">
                  暂无已启用 B站 账号
                </div>
                <div
                  v-for="acc in enabledBiliAccounts"
                  :key="acc.mid"
                  class="account-row"
                  :class="{ active: acc.is_current === 1 }"
                  @click="switchAccount(acc.mid)"
                >
                  <img :src="acc.face || 'https://static.hdslb.com/images/member/noface.gif'" class="acc-face" />
                  <div class="acc-info">
                    <div class="acc-name-line">
                      <span class="acc-name">{{ acc.uname }}</span>
                      <span v-if="acc.is_current === 1" class="tag-current">当前</span>
                    </div>
                    <span class="acc-uid">UID: {{ acc.mid }}</span>
                  </div>
                </div>
              </div>
              <div style="padding: 6px 8px; border-top: 1px solid var(--border-color); display: flex; justify-content: space-between;">
                <button class="btn-micro primary" style="width: 100%; justify-content: center;" @click="showAccountPopover = false; switchToAccountCenter('bili')">
                  <AppIcon name="person" :size="11" /><span>打开账号中心管理登录</span>
                </button>
              </div>
            </div>
          </div>

          <!-- 侧边栏模式切换 (关注更新 VS 我的收藏夹 VS 链接采集) -->
          <div class="sidebar-mode-switcher">
            <button
              class="mode-btn"
              :class="{ active: activeMode === 'follow' }"
              @click="switchMode('follow')"
              title="UP主关注更新"
            >
              <AppIcon name="group" :size="13" />
              <span>关注UP</span>
            </button>
            <button
              class="mode-btn"
              :class="{ active: activeMode === 'fav' }"
              @click="switchMode('fav')"
              title="我的收藏夹"
            >
              <AppIcon name="star" :size="13" />
              <span>收藏夹</span>
            </button>
            <button
              class="mode-btn"
              :class="{ active: activeMode === 'link' }"
              @click="switchMode('link')"
              title="链接解析与采集"
            >
              <AppIcon name="link" :size="13" />
              <span>链接解析</span>
            </button>
          </div>

          <!-- 1. 关注博主模式 -->
          <template v-if="activeMode === 'follow'">
            <div class="sidebar-section">
              <div class="section-title">
                <span>关注分组</span>
                <button class="btn-icon" title="刷新分组" @click="loadTags">↻</button>
              </div>
              <nav class="nav-list">
                <!-- 特别关注 -->
                <button
                  class="nav-item"
                  :class="{ active: currentTagId === -10 && !isAll }"
                  @click="switchGroup(-10, false)"
                >
                  <span class="nav-icon"><AppIcon name="star" :size="13" /></span>
                  <span class="nav-label">特别关注</span>
                  <span class="nav-tag-badge">主要</span>
                </button>

                <!-- 默认分组 -->
                <button
                  class="nav-item"
                  :class="{ active: currentTagId === 0 && !isAll }"
                  @click="switchGroup(0, false)"
                >
                  <span class="nav-icon"><AppIcon name="folder" :size="13" /></span>
                  <span class="nav-label">默认分组</span>
                </button>

                <!-- 全量关注 -->
                <button
                  class="nav-item"
                  :class="{ active: isAll }"
                  @click="switchGroup(0, true)"
                >
                  <span class="nav-icon"><AppIcon name="group" :size="13" /></span>
                  <span class="nav-label">全部关注</span>
                </button>

                <!-- 自定义分组列表 -->
                <button
                  v-for="t in tags.filter(x => x.tagid > 0)"
                  :key="t.tagid"
                  class="nav-item"
                  :class="{ active: currentTagId === t.tagid && !isAll }"
                  @click="switchGroup(t.tagid, false)"
                >
                  <span class="nav-icon"><AppIcon name="tag" :size="13" /></span>
                  <span class="nav-label">{{ t.name }}</span>
                  <span class="nav-count">{{ t.count }}</span>
                </button>
              </nav>
            </div>

            <!-- 本组关注博主头像/列表 -->
            <div class="sidebar-section up-list-section">
              <div class="section-title">
                <span>本组博主 ({{ upSearchKeyword.trim() ? `${filteredBloggers.length}/${bloggers.length}` : bloggers.length }})</span>
                <button
                  v-if="selectedUpMid"
                  class="btn-clear-filter"
                  @click="selectedUpMid = null; loadVideos(false)"
                  title="清除博主筛选"
                >
                  <AppIcon name="close" :size="12" /> 全部
                </button>
              </div>
              <!-- 本组 UP主 实时搜索检索框 -->
              <div class="up-search-bar">
                <AppIcon name="search" :size="12" class="up-search-icon" />
                <input
                  v-model="upSearchKeyword"
                  type="text"
                  placeholder="搜索本组 UP主昵称 / UID..."
                  class="up-search-input"
                />
                <button
                  v-if="upSearchKeyword"
                  class="up-search-clear"
                  @click="upSearchKeyword = ''"
                  title="清空搜索"
                >×</button>
              </div>
              <!-- 本地免关注订阅输入框 -->
              <div style="display: flex; gap: 6px; margin-bottom: 10px;">
                <input
                  v-model="localSubInput"
                  type="text"
                  placeholder="输入UID或空间链接订阅..."
                  style="flex: 1; min-width: 0; padding: 5px 8px; border-radius: 6px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-main); font-size: 11px;"
                  @keyup.enter="handleSubscribeLocalUp"
                />
                <button
                  class="btn-clear-filter"
                  style="padding: 4px 8px; white-space: nowrap;"
                  :disabled="subscribingUp || !localSubInput.trim()"
                  @click="handleSubscribeLocalUp"
                  title="免关注直接添加该UP主到本地订阅列表"
                >
                  {{ subscribingUp ? "..." : "+ 订阅" }}
                </button>
              </div>
              <div class="up-scroll-box">
                <div v-if="filteredBloggers.length === 0 && bloggers.length > 0" class="sidebar-empty-tip" style="padding: 12px 6px; text-align: center;">
                  <span>未找到匹配的 UP 主</span>
                </div>
                <div
                  v-for="up in filteredBloggers"
                  :key="up.mid"
                  class="up-mini-card" role="button" tabindex="0"
                  :class="{ active: selectedUpMid === up.mid }"
                  @click="selectUp(up.mid)"
                  :title="`点击仅查看 ${up.uname} 的视频 (UID: ${up.mid})`"
                >
                  <img
                    :src="formatPicUrl(up.face)"
                    referrerpolicy="no-referrer"
                    @error="(e) => onImgError(e, up.face)"
                    class="up-avatar"
                  />
                  <div class="up-info">
                    <span class="up-name" :title="up.uname">{{ up.uname }}</span>
                    <span v-if="up.is_monitored === 0" class="up-paused-tag" title="该UP日常投稿已暂停自动监听">已暂停</span>
                  </div>
                </div>
              </div>
            </div>
          </template>

          <!-- 2. 我的收藏夹模式 -->
          <template v-else>
            <div class="sidebar-section">
              <div class="section-title">
                <span>我的收藏夹 ({{ favFolders.length }})</span>
                <button class="btn-icon" title="从B站同步最新收藏夹" @click="loadFavFolders(true)">↻</button>
              </div>
              <nav class="nav-list">
                <div v-if="favFolders.length === 0" class="sidebar-empty-tip">
                  <span>暂无收藏夹数据，请点击刷新。</span>
                </div>
                <button
                  v-for="f in favFolders"
                  :key="f.id"
                  class="nav-item"
                  :class="{ active: currentFavId === f.id }"
                  @click="switchFavFolder(f.id)"
                >
                  <span class="nav-icon"><AppIcon name="folder" :size="13" /></span>
                  <span class="nav-label" :title="f.title">{{ f.title }}</span>
                  <span class="nav-count">{{ f.media_count }}</span>
                </button>
              </nav>
            </div>
          </template>
        </template>

        <!-- 分支 C：账号中心二级侧边栏 -->
        <template v-else-if="activePlatform === 'accounts'">
          <div class="zhihu-secondary-panel">
            <div class="zhihu-sec-header">
              <div class="zhihu-sec-title-row">
                <span style="display:inline-flex;align-items:center;gap:4px;"><AppIcon name="group" :size="13" />账号中心导航</span>
                <button class="btn-micro" @click="loadAccounts(); loadZhihuAccounts()">刷新</button>
              </div>
            </div>
            <div class="zhihu-sec-list">
              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: accountCenterFilter === 'all' }"
                @click="accountCenterFilter = 'all'"
              >
                <div class="zhihu-src-avatar"><AppIcon name="group" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">全部账号</div>
                  <div class="zhihu-src-sub">知乎 + 哔哩哔哩汇总管理</div>
                </div>
                <span class="sub-badge zhihu">{{ zhihuAccounts.length + accounts.length }}</span>
              </div>

              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: accountCenterFilter === 'zhihu' }"
                @click="accountCenterFilter = 'zhihu'"
              >
                <div class="zhihu-src-avatar"><img src="/zhihu.png" style="width:20px;height:20px;border-radius:4px;" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">知乎账号</div>
                  <div class="zhihu-src-sub">已启用 {{ enabledZhihuAccounts.length }} / 共 {{ zhihuAccounts.length }} 个</div>
                </div>
                <span class="sub-badge zhihu">{{ zhihuAccounts.length }}</span>
              </div>

              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: accountCenterFilter === 'bili' }"
                @click="accountCenterFilter = 'bili'"
              >
                <div class="zhihu-src-avatar"><img src="/bilibili.png" style="width:20px;height:20px;border-radius:4px;" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">哔哩哔哩账号</div>
                  <div class="zhihu-src-sub">已启用 {{ enabledBiliAccounts.length }} / 共 {{ accounts.length }} 个</div>
                </div>
                <span class="sub-badge bili">{{ accounts.length }}</span>
              </div>


            </div>
          </div>
        </template>

        <!-- 分支 D：任务队列与运行日志二级侧边栏（含按账号筛选） -->
        <template v-else-if="activePlatform === 'tasks'">
          <div class="zhihu-secondary-panel">
            <div class="zhihu-sec-header">
              <div class="zhihu-sec-title-row">
                <span style="display:inline-flex;align-items:center;gap:4px;"><AppIcon name="inbox" :size="13" />任务状态视图</span>
                <button class="btn-micro" @click="pollTasks(); loadSystemLogs()">刷新</button>
              </div>
            </div>
            <div class="zhihu-sec-list">
              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: taskViewSubTab === 'all' }"
                @click="switchToTasksPlatform('all')"
              >
                <div class="zhihu-src-avatar"><AppIcon name="inbox" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">全部任务</div>
                  <div class="zhihu-src-sub">知乎图文 + B站音视频</div>
                </div>
                <span class="sub-badge zhihu">{{ allTasks.length }}</span>
              </div>

              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: taskViewSubTab === 'running' }"
                @click="switchToTasksPlatform('running')"
              >
                <div class="zhihu-src-avatar"><AppIcon name="clock" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">进行中</div>
                  <div class="zhihu-src-sub">下载中与排队中</div>
                </div>
                <span v-if="runningCount > 0" class="badge-pulse">{{ runningCount }}</span>
              </div>

              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: taskViewSubTab === 'success' }"
                @click="switchToTasksPlatform('success')"
              >
                <div class="zhihu-src-avatar"><AppIcon name="check" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">已完成</div>
                  <div class="zhihu-src-sub">已保存至本地目录</div>
                </div>
                <span class="sub-badge emerald">{{ successCount }}</span>
              </div>

              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: taskViewSubTab === 'failed' }"
                @click="switchToTasksPlatform('failed')"
              >
                <div class="zhihu-src-avatar"><AppIcon name="close" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">失败任务</div>
                  <div class="zhihu-src-sub">支持批量重试</div>
                </div>
                <span v-if="failedCount > 0" class="sub-badge bili">{{ failedCount }}</span>
              </div>

              <div
                class="zhihu-source-item" role="button" tabindex="0"
                :class="{ active: taskViewSubTab === 'logs' }"
                @click="switchToTasksPlatform('logs')"
              >
                <div class="zhihu-src-avatar"><AppIcon name="file" :size="15" /></div>
                <div class="zhihu-src-info">
                  <div class="zhihu-src-name">运行日志</div>
                  <div class="zhihu-src-sub">后台请求与状态记录</div>
                </div>
              </div>

              <!-- 按账号过滤任务 -->
              <div v-if="taskViewSubTab !== 'logs'" style="margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border-color);">
                <div style="font-size: 11px; font-weight: 700; color: var(--text-muted); padding: 0 4px 6px;">按账号展示任务</div>
                <div
                  class="zhihu-source-item" role="button" tabindex="0"
                  :class="{ active: taskAccountFilter === 'all' }"
                  @click="taskAccountFilter = 'all'"
                >
                  <div class="zhihu-src-avatar"><AppIcon name="group" :size="14" /></div>
                  <div class="zhihu-src-info">
                    <div class="zhihu-src-name">全部账号任务</div>
                  </div>
                  <span class="sub-badge zhihu">{{ allTasks.length }}</span>
                </div>
                <div
                  v-for="accItem in taskAccountList"
                  :key="accItem.key"
                  class="zhihu-source-item" role="button" tabindex="0"
                  :class="{ active: taskAccountFilter === accItem.key }"
                  @click="taskAccountFilter = accItem.key"
                >
                  <div class="zhihu-src-avatar" style="font-size: 11px;">{{ accItem.platform === 'zhihu' ? '知' : 'B' }}</div>
                  <div class="zhihu-src-info">
                    <div class="zhihu-src-name">{{ accItem.name }}</div>
                    <div class="zhihu-src-sub">{{ accItem.platform === 'zhihu' ? '知乎账号' : 'B站账号' }}</div>
                  </div>
                  <span class="sub-badge" :class="accItem.platform === 'zhihu' ? 'zhihu' : 'bili'">{{ accItem.count }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 分支 E：系统设置中心二级侧边栏 -->
        <template v-else-if="activePlatform === 'settings'">
          <div class="zhihu-secondary-panel settings-secondary-panel">
            <div class="zhihu-sec-header">
              <div class="zhihu-sec-title-row">
                <span class="settings-sec-title">
                  <AppIcon name="settings" :size="14" />
                  <span>系统设置</span>
                </span>
                <button class="btn-micro" @click="closeSettings" title="返回主工作台">
                  <AppIcon name="arrow_back" :size="11" />
                  <span>返回</span>
                </button>
              </div>
            </div>

            <!-- 设置 Tab 选项导航 (Google M3 规范，零表情符号) -->
            <div class="settings-subnav-list">
              <button
                class="settings-subnav-item"
                :class="{ active: activeSettingsTab === 'download' }"
                @click="switchSettingsTab('download')"
              >
                <div class="settings-subnav-icon"><AppIcon name="download" :size="15" /></div>
                <div class="settings-subnav-text">
                  <div class="subnav-title">下载与存储</div>
                </div>
              </button>

              <button
                class="settings-subnav-item"
                :class="{ active: activeSettingsTab === 'autocheck' }"
                @click="switchSettingsTab('autocheck')"
              >
                <div class="settings-subnav-icon"><AppIcon name="refresh" :size="15" /></div>
                <div class="settings-subnav-text">
                  <div class="subnav-title">刷新与下载</div>
                </div>
              </button>

              <button
                class="settings-subnav-item"
                :class="{ active: activeSettingsTab === 'limiter' }"
                @click="switchSettingsTab('limiter')"
              >
                <div class="settings-subnav-icon"><AppIcon name="speed" :size="15" /></div>
                <div class="settings-subnav-text">
                  <div class="subnav-title">请求频率</div>
                </div>
              </button>

              <button
                class="settings-subnav-item"
                :class="{ active: activeSettingsTab === 'appearance' }"
                @click="switchSettingsTab('appearance')"
              >
                <div class="settings-subnav-icon"><AppIcon name="palette" :size="15" /></div>
                <div class="settings-subnav-text">
                  <div class="subnav-title">外观</div>
                </div>
              </button>

              <button
                class="settings-subnav-item"
                :class="{ active: activeSettingsTab === 'about' }"
                @click="switchSettingsTab('about')"
              >
                <div class="settings-subnav-icon"><AppIcon name="info" :size="15" /></div>
                <div class="settings-subnav-text">
                  <div class="subnav-title">关于与更新</div>
                </div>
                <span v-if="updateInfo?.has_update" class="settings-update-dot" title="有可用新版本"></span>
              </button>
            </div>
          </div>
        </template>
</aside>

      <!-- ==================== 第 3 栏：中间主内容区（知乎图文流 / B站视频流） ==================== -->
      <main class="content-panel">
        <!-- 分支 A：知乎主内容工作区 -->
        <template v-if="activePlatform === 'zhihu'">
          <!-- 知乎顶部操作与筛选栏 -->
          <div class="zhihu-main-toolbar">
            <div class="zhihu-toolbar-top">
              <div style="display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;">
                <h2 style="font-size: 16px; font-weight: 700; margin: 0;">{{ currentZhihuHeader.title }}</h2>
                <span v-if="currentZhihuHeader.desc" style="font-size: 12px; color: var(--text-muted);">{{ currentZhihuHeader.desc }}</span>
                <button
                  v-if="currentZhihuHeader.metaTitle"
                  class="btn-micro"
                  @click="showZhihuMetaBox = !showZhihuMetaBox"
                >
                  <AppIcon name="info" :size="11" />
                  <span>{{ currentZhihuHeader.metaTitle }}</span>
                </button>
              </div>

              <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
                <!-- 选中内容后才出现「保存所选 (N)」 -->
                <button
                  v-if="selectedZhihuIds.length > 0"
                  class="btn btn-primary"
                  style="background: var(--sys-accent-blue);"
                  @click="handleBatchArchiveZhihu"
                  title="将勾选的知乎内容批量保存到本地"
                >
                  <AppIcon name="download" :size="12" /> <span>保存所选 ({{ selectedZhihuIds.length }})</span>
                </button>

                <button
                  v-if="zhihuModule !== 'single'"
                  class="btn btn-secondary"
                  :disabled="syncingZhihuSource"
                  @click="handleSyncCurrentZhihuView(false)"
                  title="刷新当前列表最新内容"
                >
                  <AppIcon name="refresh" :size="13" :class="{ spinning: syncingZhihuSource }" />
                  <span>{{ syncingZhihuSource ? "刷新中..." : "刷新" }}</span>
                </button>

                <!-- 更多下拉菜单（收纳低频操作） -->
                <div v-if="zhihuModule !== 'single' || zhihuContents.length > 0" style="position: relative;">
                  <button class="btn btn-secondary" @click="showZhihuMoreMenu = !showZhihuMoreMenu">
                    <span>更多 ▾</span>
                  </button>
                  <div
                    v-if="showZhihuMoreMenu"
                    class="toolbar-dropdown-menu"
                    @click="showZhihuMoreMenu = false"
                  >
                    <button
                      v-if="zhihuModule !== 'single'"
                      class="dropdown-menu-item"
                      :disabled="syncingZhihuSource"
                      @click="handleSyncCurrentZhihuView(true)"
                    >
                      <AppIcon name="history" :size="13" />
                      <span>获取历史内容</span>
                    </button>
                    <button class="dropdown-menu-item" @click="handleMarkAllZhihuRead">
                      <AppIcon name="check" :size="13" />
                      <span>全部标为已读</span>
                    </button>
                    <button class="dropdown-menu-item" @click="openFolder()">
                      <AppIcon name="folder" :size="13" />
                      <span>打开保存文件夹</span>
                    </button>
                    <button
                      v-if="currentZhihuHeader.metaTitle"
                      class="dropdown-menu-item"
                      @click="showZhihuMetaBox = !showZhihuMetaBox"
                    >
                      <AppIcon name="info" :size="13" />
                      <span>查看{{ currentZhihuHeader.metaTitle }}</span>
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <!-- 作者资料 / 收藏夹信息折叠条 -->
            <div
              v-if="showZhihuMetaBox && currentZhihuHeader.metaDetails"
              style="margin-top: 8px; padding: 8px 12px; border-radius: 8px; background: var(--bg-input); border: 1px solid var(--border-color); font-size: 12px; color: var(--text-secondary); display: flex; justify-content: space-between; align-items: center;"
            >
              <span><strong>{{ currentZhihuHeader.metaTitle }}：</strong>{{ currentZhihuHeader.metaDetails }}</span>
              <button class="btn-micro" @click="showZhihuMetaBox = false">收起</button>
            </div>

            <!-- 单链接解析输入条 (在 single 模块下常驻展示) -->
            <div v-if="zhihuModule === 'single'" style="display: flex; gap: 10px; margin-top: 10px;">
              <input
                v-model="zhihuSingleUrl"
                type="text"
                placeholder="粘贴回答、文章或问题链接"
                style="flex: 1; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-main); font-size: 13px;"
                @keyup.enter="handleParseZhihuSingleUrl"
              />
              <button class="btn btn-primary" style="background: var(--sys-accent-blue);" :disabled="parsingZhihuUrl" @click="handleParseZhihuSingleUrl">
                <AppIcon name="link" :size="13" /> <span>{{ parsingZhihuUrl ? "解析中..." : "解析" }}</span>
              </button>
            </div>

            <!-- 知乎内容类型与状态过滤条 (链接解析模式下有结果才显示) -->
            <div v-if="zhihuModule !== 'single' || zhihuContents.length > 0" class="zhihu-filter-row">
              <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
                <button class="zhihu-filter-pill" :class="{ active: zhihuContentTypeFilter === 'all' }" @click="zhihuContentTypeFilter = 'all'; loadZhihuContents()">
                  全部 ({{ zhihuContents.length }})
                </button>
                <button class="zhihu-filter-pill" :class="{ active: zhihuContentTypeFilter === 'answer' }" @click="zhihuContentTypeFilter = 'answer'; loadZhihuContents()">
                  <AppIcon name="file" :size="12" /><span>回答</span>
                </button>
                <button class="zhihu-filter-pill" :class="{ active: zhihuContentTypeFilter === 'article' }" @click="zhihuContentTypeFilter = 'article'; loadZhihuContents()">
                  <AppIcon name="book" :size="12" /><span>文章</span>
                </button>
                <button class="zhihu-filter-pill" :class="{ active: zhihuContentTypeFilter === 'zvideo' }" @click="zhihuContentTypeFilter = 'zvideo'; loadZhihuContents()">
                  <AppIcon name="play" :size="12" /><span>视频</span>
                </button>
                <span style="width: 1px; height: 14px; background: var(--border-color); margin: 0 4px;"></span>
                <button class="zhihu-filter-pill" :class="{ active: zhihuOnlyUnread }" @click="zhihuOnlyUnread = !zhihuOnlyUnread; loadZhihuContents()">
                  <span>未读</span>
                </button>
                <button class="zhihu-filter-pill" :class="{ active: zhihuOnlyArchived }" @click="zhihuOnlyArchived = !zhihuOnlyArchived; loadZhihuContents()">
                  <AppIcon name="check" :size="12" /><span>已保存</span>
                </button>
              </div>

              <div style="display: flex; gap: 8px; align-items: center;">
                <button class="btn-micro" @click="selectedZhihuIds = zhihuContents.filter(x => !x.is_paid && !x.is_restricted).map(x => x.content_id)">全选</button>
                <button v-if="selectedZhihuIds.length > 0" class="btn-micro" @click="selectedZhihuIds = []">取消选择</button>
              </div>
            </div>
          </div>

          <!-- 知乎图文摘要卡片列表（区分离线、加载异常、首次引导、无搜索结果与普通空列表） -->
          <div class="zhihu-cards-container">
            <div v-if="loadingZhihuContents" class="loading-state">
              <div class="spinner"></div>
              <p>正在加载知乎图文列表...</p>
            </div>

            <!-- 状态 1：后台服务离线 -->
            <div v-else-if="!serverOnline" class="empty-state error-state-box">
              <div class="state-title">本地后台服务尚未连接</div>
              <p class="state-desc">无法读取本地数据库与知乎采集服务，请确认后台进程正在运行后重试。</p>
              <button type="button" class="btn btn-primary" @click="initServerConnection()">重新连接服务</button>
            </div>

            <!-- 状态 2：接口请求异常 -->
            <div v-else-if="zhihuLoadError" class="empty-state error-state-box">
              <div class="state-title">内容加载失败</div>
              <p class="state-desc">{{ zhihuLoadError }}</p>
              <div style="display: flex; gap: 8px; justify-content: center;">
                <button type="button" class="btn btn-primary" @click="loadZhihuContents()">重试加载</button>
                <button type="button" class="btn btn-secondary" @click="switchToAccountCenter('zhihu')">检查账号状态</button>
              </div>
            </div>

            <!-- 状态 3：存在搜索词或筛选条件但无匹配结果 -->
            <div
              v-else-if="zhihuContents.length === 0 && (searchKeyword.trim() || zhihuContentTypeFilter !== 'all' || zhihuOnlyUnread || zhihuOnlyArchived)"
              class="empty-state"
            >
              <div class="state-title">未找到匹配的知乎内容</div>
              <p class="state-desc">
                当前筛选条件{{ searchKeyword.trim() ? `（关键词：“${searchKeyword.trim()}”）` : '' }}下没有匹配结果。
              </p>
              <button type="button" class="btn btn-secondary" @click="clearZhihuFiltersAndSearch">清空搜索与筛选条件</button>
            </div>

            <!-- 状态 4：首次使用四步引导卡片（未登录或尚未导入任何来源） -->
            <div
              v-else-if="zhihuContents.length === 0 && zhihuModule !== 'single' && (!zhihuUser.is_login || (zhihuCreators.length === 0 && zhihuCollections.length === 0))"
              class="onboarding-guide-card"
            >
              <div class="onboarding-header">
                <h3>开始使用知乎采集工作台</h3>
                <p>按以下 4 个步骤完成配置，即可拉取并离线归档知乎回答与专栏文章：</p>
              </div>
              <div class="onboarding-steps">
                <div class="onboarding-step" :class="{ done: serverOnline }">
                  <span class="step-num">{{ serverOnline ? '✓' : '1' }}</span>
                  <div class="step-body">
                    <div class="step-title">连接本地服务</div>
                    <div class="step-desc">{{ serverOnline ? '本地 API 与 SQLite 存储已就绪' : '等待连接本地服务' }}</div>
                  </div>
                </div>
                <div class="onboarding-step" :class="{ done: zhihuUser.is_login, current: serverOnline && !zhihuUser.is_login }">
                  <span class="step-num">{{ zhihuUser.is_login ? '✓' : '2' }}</span>
                  <div class="step-body">
                    <div class="step-title">添加知乎账号</div>
                    <div class="step-desc">{{ zhihuUser.is_login ? `已登录：${zhihuUser.uname}` : '支持 App 扫码、浏览器窗口或导入 Cookie' }}</div>
                  </div>
                  <button v-if="!zhihuUser.is_login" type="button" class="btn btn-primary btn-sm" @click="openZhihuLoginModal('qrcode')">添加账号</button>
                </div>
                <div class="onboarding-step" :class="{ done: zhihuCreators.length > 0 || zhihuCollections.length > 0, current: zhihuUser.is_login && zhihuCreators.length === 0 && zhihuCollections.length === 0 }">
                  <span class="step-num">{{ (zhihuCreators.length > 0 || zhihuCollections.length > 0) ? '✓' : '3' }}</span>
                  <div class="step-body">
                    <div class="step-title">导入订阅来源</div>
                    <div class="step-desc">一键同步关注的答主、收藏夹，或在左侧输入链接添加</div>
                  </div>
                  <button
                    v-if="zhihuUser.is_login && zhihuModule === 'creators'"
                    type="button"
                    class="btn btn-primary btn-sm"
                    :disabled="syncingAccountCreators"
                    @click="handleSyncAccountCreators"
                  >
                    {{ syncingAccountCreators ? '同步中...' : '同步关注答主' }}
                  </button>
                  <button
                    v-else-if="zhihuUser.is_login && zhihuModule === 'collections'"
                    type="button"
                    class="btn btn-primary btn-sm"
                    :disabled="syncingAccountCollections"
                    @click="handleSyncAccountCollections"
                  >
                    {{ syncingAccountCollections ? '同步中...' : '同步收藏夹' }}
                  </button>
                </div>
                <div class="onboarding-step">
                  <span class="step-num">4</span>
                  <div class="step-body">
                    <div class="step-title">保存第一条内容</div>
                    <div class="step-desc">点击卡片上的「保存」或勾选多条后点击「保存所选」归档到本地</div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 状态 5：已添加来源但当前列表为空 -->
            <div v-else-if="zhihuContents.length === 0" class="empty-state">
              <div class="state-title">{{ zhihuModule === 'single' ? '等待解析知乎链接' : '当前来源暂无已缓存内容' }}</div>
              <p class="state-desc">
                {{ zhihuModule === 'single' ? '在上方输入框粘贴知乎回答、专栏文章或问题链接并点击「解析」。' : '点击下方按钮立即拉取当前来源的最新知乎回答与文章。' }}
              </p>
              <button
                v-if="zhihuModule !== 'single'"
                type="button"
                class="btn btn-primary"
                :disabled="syncingZhihuSource"
                @click="handleSyncCurrentZhihuView(false)"
              >
                {{ syncingZhihuSource ? '正在拉取...' : '刷新当前内容' }}
              </button>
            </div>

            <div v-else class="zhihu-card-list">
              <div
                v-for="item in zhihuContents"
                :key="`${item.source_type}_${item.source_id}_${item.content_id}`"
                class="zhihu-post-card"
                :class="{ selected: activeReaderItem?.content_id === item.content_id }"
                @click="openZhihuReader(item)"
              >
                <div class="zhihu-card-top">
                  <div class="zhihu-card-title-group">
                    <input
                      type="checkbox"
                      :value="item.content_id"
                      v-model="selectedZhihuIds"
                      :disabled="item.is_paid === 1 || !!item.is_restricted"
                      @click.stop
                    />
                    <span class="zhihu-type-tag" :class="item.content_type">
                      {{ item.content_type === 'article' ? '专栏文章' : item.content_type === 'zvideo' ? '视频' : '知乎回答' }}
                    </span>
                    <span v-if="item.access_pending" class="zhihu-yanxuan-badge" title="查看详情或归档时重新确认">待复核</span>
                    <span v-if="item.is_paid === 1 || item.is_restricted" class="zhihu-yanxuan-badge" :title="item.paid_reason || item.restrict_reason || '知乎盐选/付费专栏，受知识产权保护禁止归档'">
                      <AppIcon name="lock" :size="11" /> 盐选受限 (禁止归档)
                    </span>
                    <span v-if="item.source_type === 'upvote'" class="zhihu-type-tag upvote"><AppIcon name="thumb_up" :size="11" /> 我赞同</span>
                    <span v-if="item.is_read === 0 && item.updated_time && item.updated_time > (item.created_time || 0)" class="zhihu-update-badge">已更新</span>
                    <span v-else-if="item.is_read === 0" class="zhihu-new-badge">NEW</span>
                    <h3 class="zhihu-card-title">{{ item.title }}</h3>
                  </div>

                  <div class="zhihu-card-actions" @click.stop>
                    <template v-if="item.is_paid === 1 || item.is_restricted">
                      <button class="btn-micro disabled-paid" disabled :title="item.paid_reason || item.restrict_reason || '知乎盐选/付费专栏内容，受版权保护已禁止爬取'">
                        <AppIcon name="lock" :size="11" /> 盐选受限
                      </button>
                    </template>
                    <template v-else-if="item.download_status === 'success'">
                      <span class="status-badge success"><AppIcon name="check" :size="11" /> 已保存</span>
                      <button v-if="item.local_file" class="btn-micro" @click="openFile(item.local_file)" title="打开本地 Markdown 文件"><AppIcon name="file" :size="11" /> 阅读MD</button>
                      <button v-if="item.local_file" class="btn-micro" @click="openFolder(item.local_file)" title="打开保存文件夹"><AppIcon name="folder" :size="11" /> 文件夹</button>
                    </template>
                    <template v-else-if="item.download_status === 'downloading'">
                      <span class="status-badge downloading"><AppIcon name="clock" :size="11" /> 保存中...</span>
                    </template>
                    <template v-else-if="item.download_status === 'interrupted'">
                      <span class="status-badge failed" title="上次归档未完成，可重新下载">已中断，可重新下载</span>
                      <button class="btn-micro primary" @click="handleArchiveZhihuSingle(item)" title="重新保存该篇回答或文章"><AppIcon name="download" :size="11" /> 重新下载</button>
                    </template>
                    <template v-else>
                      <button class="btn-micro primary" @click="handleArchiveZhihuSingle(item)" title="将该篇回答或文章保存到本地"><AppIcon name="download" :size="11" /> 保存</button>
                    </template>
                    <button class="btn-micro" @click="openInBrowser(item.content_url)" title="在浏览器打开知乎原帖">↗ 原帖</button>
                  </div>
                </div>

                <p class="zhihu-card-excerpt">{{ item.excerpt || "点击在右侧阅读器加载完整图文内容..." }}</p>

                <div class="zhihu-card-meta">
                  <div style="display: flex; gap: 14px; align-items: center;">
                    <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="person" :size="12" />{{ item.author_name }}</span>
                    <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="thumb_up" :size="12" />{{ item.voteup_count }} 赞同</span>
                    <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="chat" :size="12" />{{ item.comment_count }} 评论</span>
                    <span v-if="item.upvote_time" style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="clock" :size="12" />赞同于: {{ formatZhihuTime(item.upvote_time) }}</span>
                    <span v-else-if="item.created_time" style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="calendar" :size="12" />发布于: {{ formatZhihuTime(item.created_time) }}</span>
                    <span v-if="item.updated_time && item.updated_time > (item.created_time || 0)" style="color: var(--sys-accent-orange); font-weight: 500;">
                      更新于: {{ formatZhihuTime(item.updated_time) }}
                    </span>
                  </div>
                  <span style="color: var(--sys-accent-blue); font-weight: 500;">点击在右侧预览完整正文 →</span>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 分支 B：原有的 B 站视频主内容面板 -->
        <template v-else-if="activePlatform === 'bili'">
        <!-- 链接采集模式 -->
        <template v-if="activeMode === 'link'">
          <div class="link-collection-panel" style="padding: 20px 24px; max-width: 960px; margin: 0 auto; width: 100%;">
            <div style="background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 20px 24px; margin-bottom: 20px;">
              <h2 style="font-size: 16px; font-weight: 700; margin: 0 0 12px; display:flex; align-items:center; gap:6px;"><AppIcon name="link" :size="16" />链接解析</h2>
              <div style="display: flex; gap: 10px;">
                <input
                  v-model="linkInput"
                  type="text"
                  placeholder="粘贴视频、合集链接或分享文字"
                  style="flex: 1; padding: 9px 14px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-main); font-size: 13px;"
                  @keyup.enter="handleParseLink"
                />
                <button class="btn btn-primary" :disabled="parsingLink" @click="handleParseLink" style="min-width: 88px;">
                  {{ parsingLink ? "解析中..." : "解析" }}
                </button>
              </div>
              <div v-if="parseError" style="color: var(--sys-accent-red); font-size: 13px; margin-top: 8px;">
                {{ parseError }}
              </div>
            </div>

            <!-- 解析结果卡片 (解析出结果后再显示下载参数与操作) -->
            <div v-if="parsedResult" style="background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 24px;">
              <div style="display: flex; gap: 20px; margin-bottom: 20px;">
                <img
                  :src="getProxyPicUrl(parsedResult.pic)"
                  style="width: 240px; height: 135px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border-color);"
                />
                <div style="flex: 1; display: flex; flex-direction: column; justify-content: space-between;">
                  <div>
                    <h3 style="font-size: 16px; font-weight: 600; line-height: 1.4; margin-bottom: 8px;">{{ parsedResult.title }}</h3>
                    <div style="font-size: 13px; color: var(--text-secondary); display: flex; gap: 16px; flex-wrap: wrap;">
                      <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="person" :size="12" />UP主: {{ parsedResult.author }}</span>
                      <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="clock" :size="12" />时长: {{ parsedResult.duration }}</span>
                      <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="calendar" :size="12" />发布: {{ parsedResult.pubdate }}</span>
                      <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="info" :size="12" />{{ parsedResult.bvid }}</span>
                    </div>
                  </div>
                  <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 12px;">
                    <div class="quality-select">
                      <span>画质:</span>
                      <select v-model="selectedQuality" @change="handleQualityPreferenceChange">
                        <option value="MAX">最高可用</option>
                        <option value="8K">8K 超高清</option>
                        <option value="DOLBY">杜比视界</option>
                        <option value="HDR">真彩 HDR</option>
                        <option value="4K">4K 超清</option>
                        <option value="1080P60">1080P 60帧</option>
                        <option value="1080P+">1080P+ 高码率</option>
                        <option value="1080P">1080P 高清</option>
                        <option value="720P">720P 高清</option>
                        <option value="480P">480P 清晰</option>
                        <option value="360P">360P 流畅</option>
                      </select>
                    </div>
                    <div class="quality-select">
                      <span>音质:</span>
                      <select v-model="selectedAudioQuality" @change="handleQualityPreferenceChange">
                        <option value="30251">Hi-Res 无损</option>
                        <option value="30250">杜比全景声</option>
                        <option value="30280">192K 高品质</option>
                        <option value="30232">132K 标准</option>
                        <option value="30216">64K 省流</option>
                      </select>
                    </div>
                    <button class="btn btn-primary" @click="handleDownloadParsedVideo(false)">
                      ⬇ 下载视频 ({{ selectedQuality === 'MAX' ? '最高可用' : selectedQuality }})
                    </button>
                    <button class="btn btn-secondary" @click="handleDownloadParsedVideo(true)">
                      <AppIcon name="headphones" :size="12" /> <span>仅音频 (.m4a)</span>
                    </button>
                    <a :href="parsedResult.url" target="_blank" class="btn btn-outline" style="text-decoration: none;">
                      ↗ 原网页
                    </a>
                  </div>
                </div>
              </div>

              <!-- 分P列表 -->
              <div v-if="parsedResult.parts && parsedResult.parts.length > 1" style="border-top: 1px solid var(--border-color); padding-top: 16px; margin-top: 16px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                  <div style="font-size: 14px; font-weight: 600;">
                    分P列表 (已选 {{ selectedPartCids.length }} / {{ parsedResult.parts.length }} P)
                  </div>
                  <div style="display: flex; gap: 8px;">
                    <button class="btn-micro" @click="toggleSelectAllParts(true)">全选</button>
                    <button class="btn-micro" @click="toggleSelectAllParts(false)">全不选</button>
                  </div>
                </div>
                <div style="max-height: 200px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px;">
                  <label
                    v-for="part in parsedResult.parts"
                    :key="part.cid"
                    style="display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; background: var(--bg-hover); border-radius: 6px; font-size: 13px; cursor: pointer;"
                  >
                    <div style="display: flex; align-items: center; gap: 8px;">
                      <input type="checkbox" :value="part.cid" v-model="selectedPartCids" />
                      <span>P{{ part.page }}. {{ part.part }}</span>
                    </div>
                    <span style="color: var(--text-muted); font-size: 12px;">{{ part.duration }}</span>
                  </label>
                </div>
              </div>

              <!-- 合集列表 -->
              <div v-if="parsedResult.season?.episodes && parsedResult.season.episodes.length > 0" style="border-top: 1px solid var(--border-color); padding-top: 16px; margin-top: 16px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                  <div style="font-size: 14px; font-weight: 600;">
                    <AppIcon name="book" :size="13" /> 所属合集: {{ parsedResult.season.title }} (已选 {{ selectedEpisodeBvids.length }} / {{ parsedResult.season.episodes.length }} 集)
                  </div>
                  <div style="display: flex; gap: 8px; align-items: center;">
                    <button class="btn-micro" @click="toggleSelectAllEpisodes(true)">全选</button>
                    <button class="btn-micro" @click="toggleSelectAllEpisodes(false)">全不选</button>
                    <button class="btn btn-batch" @click="handleDownloadSelectedEpisodes(audioOnly)">
                      ⬇ 下载已选合集视频 ({{ selectedEpisodeBvids.length }})
                    </button>
                  </div>
                </div>
                <div style="max-height: 240px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px;">
                  <label
                    v-for="ep in parsedResult.season.episodes"
                    :key="ep.bvid"
                    style="display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: var(--bg-hover); border-radius: 6px; font-size: 13px; cursor: pointer;"
                  >
                    <div style="display: flex; align-items: center; gap: 10px;">
                      <input type="checkbox" :value="ep.bvid" v-model="selectedEpisodeBvids" />
                      <span style="font-weight: 500;">{{ ep.title }}</span>
                      <span style="color: var(--text-muted); font-size: 11px;">{{ ep.bvid }}</span>
                    </div>
                    <span style="color: var(--text-muted); font-size: 12px;">{{ ep.duration }}</span>
                  </label>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 关注与收藏模式 -->
        <template v-else>
        <!-- 顶部紧凑操作栏（合并单UP主头像名称、收藏夹标题与关注分组） -->
        <div class="toolbar bili-unified-toolbar">
          <div class="toolbar-left" style="gap: 10px;">
            <div v-if="activeMode === 'follow' && selectedUpMid && selectedUpAuthor?.face" class="up-avatar-mini">
              <img :src="formatPicUrl(selectedUpAuthor.face)" referrerpolicy="no-referrer" />
            </div>
            <h2 style="font-size: 16px; font-weight: 700; margin: 0; color: var(--text-main);">{{ currentBiliContextTitle }}</h2>
            <span class="stats-badge">共 <strong>{{ filteredVideos.length }}</strong> 条视频</span>

            <!-- 单UP主视图：投稿 / 合集 切换 -->
            <div v-if="activeMode === 'follow' && selectedUpMid" class="segmented-control" style="margin-left: 6px;">
              <button :class="{ active: !showUpCollectionsPanel }" @click="showUpCollectionsPanel = false">
                <span>投稿</span>
              </button>
              <button :class="{ active: showUpCollectionsPanel }" @click="showUpCollectionsPanel = true">
                <span>合集 ({{ upCollections.length }})</span>
              </button>
            </div>
          </div>

          <div class="toolbar-right" style="gap: 8px; position: relative;">
            <!-- 多选快捷控制与「下载所选 (N)」闭环 -->
            <template v-if="filteredVideos.length > 0">
              <button
                type="button"
                class="btn-micro"
                @click="toggleSelectAllBiliVideos(selectedBiliBvids.length === 0)"
                title="全选或取消选择当前列表可下载视频"
              >
                {{ selectedBiliBvids.length > 0 ? `取消选择 (${selectedBiliBvids.length})` : "全选" }}
              </button>
              <button
                v-if="selectedBiliBvids.length > 0"
                type="button"
                class="btn btn-primary"
                style="background: var(--sys-accent-pink);"
                @click="handleBatchDownloadSelectedBili"
                :title="`将勾选的 ${selectedBiliBvids.length} 个B站视频加入下载队列`"
              >
                <AppIcon name="download" :size="12" />
                <span>下载所选 ({{ selectedBiliBvids.length }})</span>
              </button>
            </template>

            <!-- 下载未下载项 (N) -->
            <button
              v-if="selectedBiliBvids.length === 0 && undownloadedVideos.length > 0"
              type="button"
              class="btn btn-batch"
              @click="handleBatchDownload"
              :title="`批量下载当前列表未下载的 ${undownloadedVideos.length} 个音视频`"
            >
              <span>⬇</span> 下载未下载项 ({{ undownloadedVideos.length }})
            </button>

            <!-- 刷新当前内容按钮 -->
            <button
              type="button"
              class="btn btn-secondary"
              :disabled="loadingVideos"
              @click="refreshCurrentView(true)"
              title="在线检查并刷新当前列表最新视频"
            >
              <span :class="{ spinning: loadingVideos }">↻</span>
              {{ loadingVideos ? "刷新中..." : "刷新内容" }}
            </button>

            <!-- 下载选项弹出菜单 -->
            <div style="position: relative;">
              <button
                class="btn btn-secondary"
                @click="showBiliDownloadPopover = !showBiliDownloadPopover; showBiliMorePopover = false"
                title="配置仅音频、视频画质与音质规格"
              >
                <AppIcon name="tune" :size="12" />
                <span>下载选项 ({{ audioOnly ? '仅音频' : selectedQuality }}) ▾</span>
              </button>

              <div v-if="showBiliDownloadPopover" class="toolbar-dropdown-menu" style="min-width: 240px; padding: 12px;" @click.stop>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                  <span style="font-size: 12px; font-weight: 600; color: var(--text-main);">仅提取音频 (.m4a)</span>
                  <label class="switch-control" style="margin: 0;">
                    <input type="checkbox" v-model="audioOnly" />
                    <span class="switch-slider"></span>
                  </label>
                </div>

                <div v-if="!audioOnly" style="margin-bottom: 10px;">
                  <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 4px;">视频画质</div>
                  <select v-model="selectedQuality" @change="handleQualityPreferenceChange" class="setting-select" style="width: 100%; padding: 5px 8px; font-size: 12px;">
                    <option value="MAX">最高可用 (自动)</option>
                    <option value="8K">8K 超高清</option>
                    <option value="DOLBY">杜比视界</option>
                    <option value="HDR">真彩 HDR</option>
                    <option value="4K">4K 超清</option>
                    <option value="1080P60">1080P 60帧</option>
                    <option value="1080P+">1080P+ 高码率</option>
                    <option value="1080P">1080P 高清</option>
                    <option value="720P">720P 高清</option>
                    <option value="480P">480P 清晰</option>
                    <option value="360P">360P 流畅</option>
                  </select>
                </div>

                <div>
                  <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 4px;">音频音质</div>
                  <select v-model="selectedAudioQuality" @change="handleQualityPreferenceChange" class="setting-select" style="width: 100%; padding: 5px 8px; font-size: 12px;">
                    <option value="30251">Hi-Res 无损</option>
                    <option value="30250">杜比全景声</option>
                    <option value="30280">192K 高品质</option>
                    <option value="30232">132K 标准</option>
                    <option value="30216">64K 省流</option>
                  </select>
                </div>
              </div>
            </div>

            <!-- 更多操作菜单 -->
            <div style="position: relative;">
              <button
                class="btn btn-secondary"
                @click="showBiliMorePopover = !showBiliMorePopover; showBiliDownloadPopover = false"
              >
                <span>更多 ▾</span>
              </button>

              <div v-if="showBiliMorePopover" class="toolbar-dropdown-menu" style="min-width: 190px;">
                <div
                  v-if="activeMode === 'follow' && selectedUpMid"
                  class="dropdown-menu-item"
                  style="justify-content: space-between;"
                  @click.stop
                  title="开启后，后台定时检查更新时会拉取该UP主的新投稿"
                >
                  <span>自动检查新投稿</span>
                  <input
                    type="checkbox"
                    :checked="currentUpMonitored"
                    @change="handleToggleUpMonitor(selectedUpMid, !currentUpMonitored)"
                  />
                </div>
                <button
                  v-if="activeMode === 'follow' && selectedUpMid"
                  class="dropdown-menu-item"
                  :disabled="loadingVideos || deepScraping"
                  @click="showBiliMorePopover = false; handleDeepScrapeUp()"
                >
                  <AppIcon name="history" :size="13" />
                  <span>{{ deepScraping ? "正在获取..." : "获取历史内容" }}</span>
                </button>
                <button
                  v-if="unreadVideosCount > 0"
                  class="dropdown-menu-item"
                  @click="showBiliMorePopover = false; handleMarkRead()"
                >
                  <AppIcon name="check" :size="13" />
                  <span>全部标为已读 ({{ unreadVideosCount }})</span>
                </button>
                <button
                  class="dropdown-menu-item"
                  @click="showBiliMorePopover = false; openFolder()"
                >
                  <AppIcon name="folder" :size="13" />
                  <span>打开保存文件夹</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- UP主合集面板 -->
        <div v-if="activeMode === 'follow' && selectedUpMid && showUpCollectionsPanel" class="up-collections-panel">
          <div class="collections-panel-header">
            <div class="collections-header-left">
              <h4>合集</h4>
            </div>
            <div class="collections-header-right">
              <button class="btn btn-secondary btn-sm" :disabled="loadingUpCollections" @click="loadUpCollections(selectedUpMid, true)">
                <span :class="{ spinning: loadingUpCollections }">↻</span>
                {{ loadingUpCollections ? "刷新中..." : "刷新" }}
              </button>
              <button class="btn-close" @click="showUpCollectionsPanel = false">✕</button>
            </div>
          </div>

          <div v-if="loadingUpCollections && upCollections.length === 0" class="collections-loading">
            <div class="spinner"></div>
            <span>正在读取合集列表...</span>
          </div>

          <div v-else-if="upCollections.length === 0" class="collections-empty">
            <p>该 UP 主暂无公开合集或系列。</p>
          </div>

          <div v-else class="collections-grid">
            <div v-for="col in upCollections" :key="`${col.collection_type}_${col.collection_id}`" class="col-card">
              <div class="col-cover-box">
                <img v-if="col.cover" :src="formatPicUrl(col.cover)" referrerpolicy="no-referrer" class="col-cover" />
                <div v-else class="col-cover-placeholder">合集封面</div>
                <span class="col-type-tag" :class="col.collection_type">{{ col.collection_type === 'season' ? '合集' : '系列' }}</span>
                <span class="col-ep-count">共 {{ col.total }} 集</span>
              </div>
              <div class="col-body">
                <h5 class="col-title" :title="col.title">{{ col.title }}</h5>
                <div class="col-controls">
                  <!-- 开关 B: 是否自动检查此合集 -->
                  <label class="col-monitor-switch" title="开启后，定时刷新时会检查该合集有无新剧集">
                    <input
                      type="checkbox"
                      :checked="!!col.is_monitored"
                      @change="handleToggleCollectionMonitor(col)"
                    />
                    <span class="switch-slider"></span>
                    <span class="switch-label">自动检查</span>
                  </label>

                  <!-- 一键下载合集 -->
                  <button
                    class="btn btn-sm btn-batch"
                    @click="handleDownloadCollection(col)"
                    :disabled="loadingUpCollections"
                    title="将该合集下的全部视频加入下载队列"
                  >
                    <span>⬇</span> 下载合集
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 视频更新网格瀑布流 -->
        <div class="video-container">
          <div v-if="loadingVideos && videos.length === 0" class="loading-state">
            <div class="spinner"></div>
            <p>正在拉取博主视频投稿列表...</p>
          </div>

          <!-- 状态 1：后台服务离线 -->
          <div v-else-if="!serverOnline" class="empty-state error-state-box">
            <div class="state-title">本地后台服务尚未连接</div>
            <p class="state-desc">无法连接本地 B站 采集服务，请确认后台服务已启动后重试。</p>
            <button type="button" class="btn btn-primary" @click="initServerConnection()">重新连接服务</button>
          </div>

          <!-- 状态 2：请求异常 -->
          <div v-else-if="biliLoadError && filteredVideos.length === 0" class="empty-state error-state-box">
            <div class="state-title">加载 B站 内容失败</div>
            <p class="state-desc">{{ biliLoadError }}</p>
            <div style="display: flex; gap: 8px; justify-content: center;">
              <button type="button" class="btn btn-primary" @click="refreshCurrentView(true)">重试加载</button>
              <button type="button" class="btn btn-secondary" @click="switchToAccountCenter('bili')">检查账号状态</button>
            </div>
          </div>

          <!-- 状态 3：搜索无结果 -->
          <div v-else-if="filteredVideos.length === 0 && searchKeyword.trim()" class="empty-state">
            <div class="state-title">未找到匹配的视频</div>
            <p class="state-desc">关键词“{{ searchKeyword.trim() }}”下没有匹配的本地或当前分组视频记录。</p>
            <button type="button" class="btn btn-secondary" @click="clearGlobalSearch">清空搜索关键词</button>
          </div>

          <!-- 状态 4：首次使用四步引导 -->
          <div
            v-else-if="filteredVideos.length === 0 && !user.is_login && bloggers.length === 0 && favFolders.length === 0"
            class="onboarding-guide-card"
          >
            <div class="onboarding-header">
              <h3>开始使用哔哩哔哩采集工作台</h3>
              <p>完成以下 4 步即可按分组追踪 UP主 投稿、收藏夹并批量下载音视频：</p>
            </div>
            <div class="onboarding-steps">
              <div class="onboarding-step" :class="{ done: serverOnline }">
                <span class="step-num">{{ serverOnline ? '✓' : '1' }}</span>
                <div class="step-body">
                  <div class="step-title">连接本地服务</div>
                  <div class="step-desc">本地多线程下载引擎与 SQLite 已连接</div>
                </div>
              </div>
              <div class="onboarding-step" :class="{ done: user.is_login, current: serverOnline && !user.is_login }">
                <span class="step-num">{{ user.is_login ? '✓' : '2' }}</span>
                <div class="step-body">
                  <div class="step-title">添加 B站 账号</div>
                  <div class="step-desc">支持 B站 App 扫码或 Cookie 导入，解锁高画质与关注列表</div>
                </div>
                <button type="button" class="btn btn-primary btn-sm" style="background: var(--sys-accent-pink);" @click="openOfficialLogin">添加账号</button>
              </div>
              <div class="onboarding-step">
                <span class="step-num">3</span>
                <div class="step-body">
                  <div class="step-title">导入关注分组或免关注订阅</div>
                  <div class="step-desc">登录后自动同步关注分组与收藏夹，也可在左侧输入 UID 直接订阅</div>
                </div>
              </div>
              <div class="onboarding-step">
                <span class="step-num">4</span>
                <div class="step-body">
                  <div class="step-title">保存第一条音视频</div>
                  <div class="step-desc">支持勾选多条视频「下载所选」或一键提取高品质 .m4a 音频</div>
                </div>
              </div>
            </div>
          </div>

          <!-- 状态 5：普通空列表 -->
          <div v-else-if="filteredVideos.length === 0" class="empty-state">
            <div class="state-title">当前分组或收藏夹暂无视频记录</div>
            <p class="state-desc">点击下方按钮立即在线检查并拉取最新视频列表。</p>
            <button type="button" class="btn btn-primary" :disabled="loadingVideos" @click="refreshCurrentView(true)">
              {{ loadingVideos ? '正在拉取...' : '刷新当前内容' }}
            </button>
          </div>

          <div v-else class="video-grid">
            <div
              v-for="v in filteredVideos"
              :key="v.bvid"
              class="video-card"
              :class="{ 'is-restricted-card': isVideoRestricted(v), 'is-selected-card': selectedBiliBvids.includes(v.bvid) }"
            >
              <!-- 封面图 (防盗链策略优化 + 左上角多选框) -->
              <div class="cover-wrapper" @click="handleMarkRead(v.bvid); openInBrowser(v.URL)">
                <label class="bili-card-checkbox" @click.stop :title="isVideoRestricted(v) ? '受限视频不可勾选下载' : '勾选以批量下载'">
                  <input
                    type="checkbox"
                    :value="v.bvid"
                    v-model="selectedBiliBvids"
                    :disabled="isVideoRestricted(v)"
                  />
                </label>
                <img
                  :src="formatPicUrl(v.封面)"
                  referrerpolicy="no-referrer"
                  @error="(e) => onImgError(e, v.封面)"
                  class="cover-img"
                  loading="lazy"
                />
                <span
                  v-if="v.is_read === 0"
                  style="position: absolute; top: 8px; left: 8px; background: var(--sys-accent-red); color: #fff; font-size: 11px; font-weight: 700; padding: 2px 7px; border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,0.4);"
                  title="后台巡检新发现的未读稿件，点击即可消去角标"
                  @click.stop="handleMarkRead(v.bvid)"
                >
                  新追更
                </span>
                <span
                  v-if="isVideoRestricted(v)"
                  class="restricted-cover-tag"
                  :title="`受限视频 (${getVideoRestrictReason(v)})：已自动禁止下载试看流`"
                >
                  🔒 {{ getVideoRestrictReason(v) }}
                </span>
                <span class="duration-tag" v-if="v.时长">{{ v.时长 }}</span>
                <span class="play-tag" v-if="v.播放量">▶ {{ v.播放量 }}</span>
              </div>

              <!-- 详情信息 -->
              <div class="card-details">
                <h3 class="video-title" :title="v.视频名字" @click="openInBrowser(v.URL)">
                  {{ v.视频名字 }}
                </h3>

                <div class="card-meta">
                  <span class="author-name" @click.stop="selectUp(v.UP_MID)" title="点击仅看该UP主">{{ v.UP名字 }}</span>
                  <span class="pubdate">{{ v.发布日期 }}</span>
                </div>

                <!-- 下载动作条 -->
                <div class="card-actions">
                  <template v-if="isVideoRestricted(v)">
                    <span class="bvid-tag" :title="v.bvid">{{ v.bvid }}</span>
                    <span class="status-badge restricted" :title="`已拦截【${getVideoRestrictReason(v)}】视频，防止下载 6 秒试看片段`">
                      🔒 {{ getVideoRestrictReason(v) }}
                    </span>
                  </template>
                  <template v-else-if="getVideoDownloadState(v)?.status === 'downloading'">
                    <div class="downloaded-group">
                      <span class="status-badge downloading" :title="`${v.bvid} · 速度: ${getVideoDownloadState(v)?.speed || '计算中...'}`">
                        <AppIcon name="clock" :size="12" class="spinning" /> {{ getVideoDownloadState(v)?.progress }}% ({{ getVideoDownloadState(v)?.speed || '下载中' }})
                      </span>
                      <div class="card-btn-pair">
                        <button class="btn-micro" @click="handleCancelSingle(getVideoDownloadState(v)?.task_key || v.bvid)" title="取消下载"><AppIcon name="close" :size="11" /> 取消</button>
                      </div>
                    </div>
                  </template>
                  <template v-else-if="getVideoDownloadState(v)?.status === 'pending'">
                    <div class="downloaded-group">
                      <span class="status-badge pending" :title="v.bvid">
                        <AppIcon name="clock" :size="11" /> 排队等待中
                      </span>
                      <div class="card-btn-pair">
                        <button class="btn-micro" @click="handleCancelSingle(getVideoDownloadState(v)?.task_key || v.bvid)" title="取消排队"><AppIcon name="close" :size="11" /> 取消</button>
                      </div>
                    </div>
                  </template>
                  <template v-else-if="getVideoDownloadState(v)?.status === 'success'">
                    <div class="downloaded-group">
                      <span class="status-badge success" :title="`${v.bvid} · ${getVideoDownloadState(v)?.file_path}`"><AppIcon name="check" :size="11" /> 已下载</span>
                      <div class="card-btn-pair">
                        <button class="btn-micro" @click="openFile(getVideoDownloadState(v)?.file_path)" title="播放本地文件">▶ 播放</button>
                        <button class="btn-micro" @click="openFolder(getVideoDownloadState(v)?.file_path)" title="在文件夹中定位"><AppIcon name="folder" :size="11" /> 定位</button>
                      </div>
                    </div>
                  </template>
                  <template v-else-if="getVideoDownloadState(v)?.status === 'failed'">
                    <span class="bvid-tag" :title="v.bvid">{{ v.bvid }}</span>
                    <div class="card-btn-pair">
                      <span class="status-badge failed" :title="getVideoDownloadState(v)?.error || '下载失败'"><AppIcon name="close" :size="11" /> {{ getVideoDownloadState(v)?.error === '已中断，可重新下载' ? '已中断，可重新下载' : '失败' }}</span>
                      <button class="btn-micro retry-btn" :disabled="getVideoDownloadState(v)?.is_stopping" @click="triggerDownload(v)" title="重新加入队列">{{ getVideoDownloadState(v)?.is_stopping ? '停止中...' : '重试' }}</button>
                    </div>
                  </template>
                  <template v-else>
                    <span class="bvid-tag" :title="v.bvid">{{ v.bvid }}</span>
                    <button class="btn btn-download" @click="triggerDownload(v)">
                      {{ audioOnly ? "⬇ 下载音频" : `⬇ 下载 ${selectedQuality === 'MAX' ? '最高画质' : selectedQuality}` }}
                    </button>
                  </template>
                </div>
              </div>
            </div>
          </div>
        </div>
        </template>
        </template>

        <!-- 分支 C：一级 Tab「账号中心」主工作区 -->
        <template v-else-if="activePlatform === 'accounts'">
          <div class="zhihu-main-toolbar">
            <div class="zhihu-toolbar-top">
              <div style="display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;">
                <h2 style="font-size: 16px; font-weight: 700; margin: 0;">
                  {{ accountCenterFilter === 'zhihu' ? '知乎账号' : accountCenterFilter === 'bili' ? 'B站账号' : '全部账号' }}
                </h2>
                <span v-if="accountCenterFilter === 'zhihu'" style="font-size: 12px; color: var(--text-muted);">
                  已启用 {{ enabledZhihuAccounts.length }} / 共 {{ zhihuAccounts.length }}
                </span>
                <span v-else-if="accountCenterFilter === 'bili'" style="font-size: 12px; color: var(--text-muted);">
                  已启用 {{ enabledBiliAccounts.length }} / 共 {{ accounts.length }}
                </span>
                <span v-else style="font-size: 12px; color: var(--text-muted);">
                  知乎 {{ zhihuAccounts.length }} · B站 {{ accounts.length }}
                </span>
              </div>

              <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                <button
                  v-if="accountCenterFilter === 'zhihu'"
                  class="btn btn-primary"
                  style="background: var(--sys-accent-blue);"
                  @click="openZhihuLoginModal('qrcode')"
                >
                  <AppIcon name="add" :size="12" /> <span>+ 添加账号</span>
                </button>
                <button
                  v-if="accountCenterFilter === 'bili'"
                  class="btn btn-primary"
                  style="background: var(--sys-accent-pink);"
                  @click="openOfficialLogin"
                >
                  <AppIcon name="add" :size="12" /> <span>+ 添加账号</span>
                </button>
                <button class="btn btn-secondary" @click="loadAccounts(); loadZhihuAccounts()">
                  <AppIcon name="refresh" :size="13" /> <span>刷新</span>
                </button>
              </div>
            </div>
          </div>

          <div class="zhihu-cards-container" style="display: flex; flex-direction: column; gap: 20px;">
            <!-- 1. 知乎账号 -->
            <div v-if="accountCenterFilter === 'all' || accountCenterFilter === 'zhihu'" class="setting-card">
              <div v-if="accountCenterFilter === 'all'" class="setting-card-header" style="padding-bottom: 12px; border-bottom: 1px solid var(--border-color);">
                <div style="display: flex; align-items: center; gap: 10px;">
                  <img src="/zhihu.png" style="width: 24px; height: 24px; border-radius: 6px;" />
                  <h3 style="font-size: 15px; font-weight: 700; margin: 0;">知乎账号</h3>
                  <span style="font-size: 12px; color: var(--text-muted);">已启用 {{ enabledZhihuAccounts.length }} / 共 {{ zhihuAccounts.length }}</span>
                </div>
                <button class="btn btn-primary btn-sm" style="background: var(--sys-accent-blue);" @click="openZhihuLoginModal('qrcode')">
                  <AppIcon name="add" :size="12" /> <span>+ 添加账号</span>
                </button>
              </div>

              <div style="margin-top: 14px; display: flex; flex-direction: column; gap: 10px;">
                <div v-if="zhihuAccounts.length === 0" class="sidebar-empty-tip" style="padding: 24px; text-align: center;">
                  暂未添加知乎账号，请点击右上角「+ 添加账号」。
                </div>
                <div
                  v-for="acc in zhihuAccounts"
                  :key="acc.url_token"
                  class="acc-card-item"
                  :class="{ current: acc.is_current === 1 }"
                  style="padding: 12px 16px;"
                >
                  <img :src="acc.face ? getProxyPicUrl(acc.face) : 'https://static.hdslb.com/images/member/noface.gif'" class="acc-card-face" />
                  <div class="acc-card-meta">
                    <div class="acc-card-title-line">
                      <span class="acc-card-name">{{ acc.uname }}</span>
                      <span v-if="acc.is_current === 1" class="tag-current-badge">当前激活</span>
                      <span v-if="acc.is_enabled === 0" class="inline-auth-status">已停用</span>
                      <span v-else class="inline-auth-status online">已启用</span>
                    </div>
                    <span class="acc-card-sub">Token: {{ acc.url_token }} {{ acc.headline ? `· ${acc.headline}` : '' }}</span>
                  </div>

                  <div style="display: flex; align-items: center; gap: 18px; flex-wrap: wrap;">
                    <label class="checkbox-label" style="font-size: 12px; cursor: pointer;" title="开启后该账号将显示在知乎侧边栏切换器中">
                      <input type="checkbox" :checked="acc.is_enabled !== 0" @change="toggleZhihuAccountFlag(acc, 'is_enabled')" />
                      <span>启用账号</span>
                    </label>
                    <label class="checkbox-label" style="font-size: 12px; cursor: pointer;" title="开启后该账号将参与后台定时刷新">
                      <input type="checkbox" :checked="acc.auto_refresh !== 0" :disabled="acc.is_enabled === 0" @change="toggleZhihuAccountFlag(acc, 'auto_refresh')" />
                      <span>参与定时刷新</span>
                    </label>
                    <div class="acc-card-actions">
                      <button
                        v-if="acc.is_current !== 1 && acc.is_enabled !== 0"
                        class="btn btn-secondary btn-sm"
                        @click="switchZhihuAccount(acc.url_token)"
                      >
                        设为当前
                      </button>
                      <button
                        class="btn btn-danger-outline btn-sm"
                        @click="removeZhihuAccount(acc.url_token, acc.uname)"
                      >
                        移除
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 2. B站账号 -->
            <div v-if="accountCenterFilter === 'all' || accountCenterFilter === 'bili'" class="setting-card">
              <div v-if="accountCenterFilter === 'all'" class="setting-card-header" style="padding-bottom: 12px; border-bottom: 1px solid var(--border-color);">
                <div style="display: flex; align-items: center; gap: 10px;">
                  <img src="/bilibili.png" style="width: 24px; height: 24px; border-radius: 6px;" />
                  <h3 style="font-size: 15px; font-weight: 700; margin: 0;">B站账号</h3>
                  <span style="font-size: 12px; color: var(--text-muted);">已启用 {{ enabledBiliAccounts.length }} / 共 {{ accounts.length }}</span>
                </div>
                <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                  <button class="btn btn-primary btn-sm" style="background: var(--sys-accent-pink);" @click="openOfficialLogin">
                    <AppIcon name="add" :size="12" /> <span>+ 添加账号</span>
                  </button>
                </div>
              </div>

              <div style="margin-top: 14px; display: flex; flex-direction: column; gap: 10px;">
                <div v-if="accounts.length === 0" class="sidebar-empty-tip" style="padding: 24px; text-align: center;">
                  暂未添加 B站 账号，请点击右上角「+ 添加账号」。
                </div>
                <div
                  v-for="acc in accounts"
                  :key="acc.mid"
                  class="acc-card-item"
                  :class="{ current: acc.is_current === 1 }"
                  style="padding: 12px 16px;"
                >
                  <img :src="acc.face || 'https://static.hdslb.com/images/member/noface.gif'" class="acc-card-face" />
                  <div class="acc-card-meta">
                    <div class="acc-card-title-line">
                      <span class="acc-card-name">{{ acc.uname }}</span>
                      <span v-if="acc.is_current === 1" class="tag-current-badge">当前激活</span>
                      <span v-if="acc.vip_type" class="vip-tag">大会员</span>
                      <span v-if="acc.is_enabled === 0" class="inline-auth-status">已停用</span>
                      <span v-else class="inline-auth-status online">已启用</span>
                    </div>
                    <span class="acc-card-sub">UID: {{ acc.mid }}</span>
                  </div>

                  <div style="display: flex; align-items: center; gap: 18px; flex-wrap: wrap;">
                    <label class="checkbox-label" style="font-size: 12px; cursor: pointer;" title="开启后该账号将显示在B站侧边栏切换器中">
                      <input type="checkbox" :checked="acc.is_enabled !== 0" @change="toggleBiliAccountFlag(acc, 'is_enabled')" />
                      <span>启用账号</span>
                    </label>
                    <label class="checkbox-label" style="font-size: 12px; cursor: pointer;" title="开启后该账号将参与后台定时刷新">
                      <input type="checkbox" :checked="acc.auto_refresh !== 0" :disabled="acc.is_enabled === 0" @change="toggleBiliAccountFlag(acc, 'auto_refresh')" />
                      <span>参与定时刷新</span>
                    </label>
                    <div class="acc-card-actions">
                      <button
                        v-if="acc.is_current !== 1 && acc.is_enabled !== 0"
                        class="btn btn-secondary btn-sm"
                        @click="switchAccount(acc.mid)"
                      >
                        设为当前
                      </button>
                      <button
                        class="btn btn-danger-outline btn-sm"
                        @click="removeAccount(acc.mid, acc.uname)"
                      >
                        移除
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 分支 D：一级 Tab「任务队列与运行日志」主工作区 -->
        <template v-else-if="activePlatform === 'tasks'">
          <div class="zhihu-main-toolbar">
            <div class="zhihu-toolbar-top">
              <div style="display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;">
                <h2 style="font-size: 16px; font-weight: 700; margin: 0;">
                  {{ taskViewSubTab === 'logs' ? '运行日志' : taskViewSubTab === 'running' ? '进行中' : taskViewSubTab === 'success' ? '已完成' : taskViewSubTab === 'failed' ? '失败任务' : '全部任务' }}
                </h2>
                <span v-if="taskViewSubTab === 'running'" style="font-size: 12px; color: var(--text-muted);">
                  下载中 {{ downloadingCount }} · 排队中 {{ pendingCount }}
                </span>
                <span v-else-if="taskViewSubTab !== 'logs'" style="font-size: 12px; color: var(--text-muted);">
                  共 {{ filteredDrawerTasks.length }} 项
                </span>
              </div>

              <!-- 顶部操作随任务子页面动态变化 -->
              <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
                <!-- 1. 运行日志子页：只保留刷新日志 -->
                <template v-if="taskViewSubTab === 'logs'">
                  <button class="btn btn-secondary" :disabled="loadingSystemLogs" @click="loadSystemLogs">
                    <AppIcon name="refresh" :size="13" :class="{ spinning: loadingSystemLogs }" />
                    <span>{{ loadingSystemLogs ? "读取中..." : "刷新日志" }}</span>
                  </button>
                </template>

                <!-- 2. 进行中子页：仅保留刷新 -->
                <template v-else-if="taskViewSubTab === 'running'">
                  <button class="btn btn-secondary" @click="pollTasks()">
                    <AppIcon name="refresh" :size="13" /> <span>刷新</span>
                  </button>
                </template>

                <!-- 3. 已完成子页：按当前筛选清除已完成记录 + 打开保存文件夹 + 刷新 -->
                <template v-else-if="taskViewSubTab === 'success'">
                  <button
                    v-if="scopedCompletedTaskIds.length > 0"
                    type="button"
                    class="btn btn-secondary"
                    @click="handleClearCompleted"
                    title="仅清除当前筛选下的已完成记录（不包含已取消或受限记录，也不会删除本地文件）"
                  >
                    <AppIcon name="trash" :size="12" />
                    <span>{{ taskAccountFilter !== 'all' || searchKeyword.trim() ? `清除当前筛选已完成 (${scopedCompletedTaskIds.length})` : `清除已完成记录 (${scopedCompletedTaskIds.length})` }}</span>
                  </button>
                  <button type="button" class="btn btn-folder" @click="openFolder()">
                    <AppIcon name="folder" :size="12" /> <span>打开保存文件夹</span>
                  </button>
                  <button type="button" class="btn btn-secondary" @click="pollTasks()">
                    <AppIcon name="refresh" :size="13" /> <span>刷新</span>
                  </button>
                </template>

                <!-- 4. 失败任务子页：明确区分「重试失败」与「恢复已取消」 -->
                <template v-else-if="taskViewSubTab === 'failed'">
                  <button
                    v-if="scopedFailedTaskIds.length > 0"
                    type="button"
                    class="btn btn-secondary"
                    style="color: var(--sys-accent-red);"
                    @click="handleRetryAllFailed"
                    title="仅重试当前筛选下状态为失败的任务"
                  >
                    <span>{{ taskAccountFilter !== 'all' || searchKeyword.trim() ? `重试当前筛选失败 (${scopedFailedTaskIds.length})` : `重试全部失败 (${scopedFailedTaskIds.length})` }}</span>
                  </button>
                  <button
                    v-if="scopedCancelledTaskIds.length > 0"
                    type="button"
                    class="btn btn-secondary"
                    @click="handleRestoreCancelled"
                    title="单独恢复当前筛选下已手动取消的任务"
                  >
                    <span>恢复已取消 ({{ scopedCancelledTaskIds.length }})</span>
                  </button>
                  <button type="button" class="btn btn-secondary" @click="pollTasks()">
                    <AppIcon name="refresh" :size="13" /> <span>刷新</span>
                  </button>
                </template>

                <!-- 5. 全部任务子页：保留刷新 + 更多菜单（按当前筛选范围执行） -->
                <template v-else>
                  <button type="button" class="btn btn-secondary" @click="pollTasks()">
                    <AppIcon name="refresh" :size="13" /> <span>刷新</span>
                  </button>
                  <div style="position: relative;">
                    <button type="button" class="btn btn-secondary" @click="showTaskMoreMenu = !showTaskMoreMenu">
                      <span>更多 ▾</span>
                    </button>
                    <div
                      v-if="showTaskMoreMenu"
                      class="toolbar-dropdown-menu"
                      @click="showTaskMoreMenu = false"
                    >
                      <button
                        v-if="scopedFailedTaskIds.length > 0"
                        type="button"
                        class="dropdown-menu-item"
                        style="color: var(--sys-accent-red);"
                        @click="handleRetryAllFailed"
                      >
                        <AppIcon name="refresh" :size="13" />
                        <span>{{ taskAccountFilter !== 'all' || searchKeyword.trim() ? `重试当前筛选失败 (${scopedFailedTaskIds.length})` : `重试全部失败 (${scopedFailedTaskIds.length})` }}</span>
                      </button>
                      <button
                        v-if="scopedCancelledTaskIds.length > 0"
                        type="button"
                        class="dropdown-menu-item"
                        @click="handleRestoreCancelled"
                      >
                        <AppIcon name="refresh" :size="13" />
                        <span>恢复已取消任务 ({{ scopedCancelledTaskIds.length }})</span>
                      </button>
                      <button
                        v-if="scopedCompletedTaskIds.length > 0"
                        type="button"
                        class="dropdown-menu-item"
                        @click="handleClearCompleted"
                        title="仅清除当前筛选下的已完成记录，不会删除已保存的本地文件"
                      >
                        <AppIcon name="trash" :size="13" />
                        <span>{{ taskAccountFilter !== 'all' || searchKeyword.trim() ? `清除当前筛选已完成 (${scopedCompletedTaskIds.length})` : `清除已完成记录 (${scopedCompletedTaskIds.length})` }}</span>
                      </button>
                      <button
                        class="dropdown-menu-item"
                        :disabled="isReconciling"
                        @click="handleManualReconcile"
                      >
                        <AppIcon name="search" :size="13" />
                        <span>{{ isReconciling ? "正在检查..." : "检查本地文件" }}</span>
                      </button>
                      <button class="dropdown-menu-item" @click="openFolder()">
                        <AppIcon name="folder" :size="13" />
                        <span>打开保存文件夹</span>
                      </button>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>

          <!-- 子视图 1：运行日志 -->
          <div v-if="taskViewSubTab === 'logs'" class="zhihu-feed-scroll" style="display: flex; flex-direction: column; gap: 12px;">
            <div style="background: var(--bg-input); border: 1px solid var(--border-color); border-radius: 10px; padding: 14px; font-family: Consolas, monospace; font-size: 12px; line-height: 1.6; color: var(--text-main); max-height: calc(100vh - 190px); overflow-y: auto;">
              <div style="display: flex; justify-content: space-between; align-items: center; padding-bottom: 8px; margin-bottom: 10px; border-bottom: 1px solid var(--border-color);">
                <span
                  class="truncate"
                  style="color: var(--text-muted); font-size: 11px; max-width: 75%;"
                  :title="systemLogFile || 'logs/app.log'"
                >
                  日志文件：{{ systemLogFile || 'logs/app.log' }}
                </span>
                <span style="color: var(--text-muted); font-size: 11px; flex-shrink: 0;">最新 {{ systemLogLines.length }} 行</span>
              </div>
              <div v-if="systemLogLines.length === 0" style="color: var(--text-muted); padding: 20px; text-align: center;">
                暂无日志记录，点击右上角「刷新日志」加载。
              </div>
              <div
                v-for="(ln, idx) in systemLogLines"
                :key="idx"
                style="white-space: pre-wrap; word-break: break-all; padding: 2px 0; border-bottom: 1px dashed var(--border-color);"
                :style="{ color: ln.includes('[WARNING]') || ln.includes('status=403') ? 'var(--sys-accent-orange)' : ln.includes('[ERROR]') ? 'var(--sys-accent-red)' : ln.includes('[HTTP RESP]') || ln.includes('status=200') ? 'var(--sys-accent-green)' : 'var(--text-main)' }"
              >{{ ln }}</div>
            </div>
          </div>

          <!-- 子视图 2：跨平台任务卡片列表 (紧凑下拉分组选择 + 显式账号筛选范围) -->
          <div v-else class="zhihu-feed-scroll">
            <div class="task-dimension-bar" style="justify-content: space-between;">
              <div style="display: flex; align-items: center; gap: 8px;">
                <span class="dimension-label">分组:</span>
                <select v-model="taskGroupingMode" class="setting-select" style="width: auto; min-width: 140px; padding: 4px 10px; font-size: 12px;">
                  <option value="flat">不分组 ({{ filteredDrawerTasks.length }})</option>
                  <option value="account">按账号</option>
                  <option value="author">按作者</option>
                  <option value="folder">按收藏夹或合集</option>
                </select>
              </div>

              <!-- 显式标明当前筛选账号范围 -->
              <div v-if="taskAccountFilter !== 'all'" style="display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--sys-accent-blue);">
                <span>当前筛选账号：<strong>{{ taskAccountFilter.replace('zhihu:', '知乎 · ').replace('bili:', 'B站 · ') }}</strong></span>
                <button class="btn-micro" @click="taskAccountFilter = 'all'">显示全部账号 ✕</button>
              </div>
            </div>

            <div v-if="reconcileNotice" class="reconcile-alert" style="margin-bottom: 12px;">
              {{ reconcileNotice }}
            </div>

            <div v-if="filteredDrawerTasks.length === 0" class="empty-state">
              <p>当前筛选下暂无下载或归档任务</p>
            </div>

            <!-- 维度 A：按账号 / 作者 / 收藏夹分组细度渲染 -->
            <div v-else-if="taskGroupingMode !== 'flat'" class="task-groups-list">
              <div
                v-for="grp in groupedTasks"
                :key="grp.groupKey"
                class="task-group-card"
              >
                <div class="task-group-header" @click="toggleGroupCollapse(grp.groupKey)">
                  <div class="group-title-left">
                    <span class="platform-logo" :class="grp.platform" style="width: 22px; height: 22px; font-size: 11px;">
                      {{ grp.platform === 'zhihu' ? '知' : 'B' }}
                    </span>
                    <span class="group-name">
                      {{ grp.groupType === 'account' ? '所属账号: ' : grp.groupType === 'author' ? '答主/UP主: ' : '收藏夹: ' }}<strong>{{ grp.groupName }}</strong>
                    </span>
                    <span class="group-meta-pill">共 {{ grp.tasks.length }} 个任务</span>
                    <span v-if="grp.running > 0" class="sub-badge zhihu">{{ grp.running }} 进行中</span>
                    <span v-if="grp.failed > 0" class="sub-badge bili">{{ grp.failed }} 失败</span>
                    <span v-if="grp.success === grp.total" class="sub-badge emerald">全部完成</span>
                  </div>
                  <div class="group-actions" @click.stop>
                    <button
                      v-if="grp.failed > 0"
                      class="btn-micro"
                      style="color: var(--sys-accent-red);"
                      @click="handleRetryGroup(grp)"
                    >
                      重试失败 ({{ grp.failed }})
                    </button>
                    <button
                      class="btn-micro"
                      @click="toggleGroupCollapse(grp.groupKey)"
                    >
                      {{ collapsedGroups[grp.groupKey] ? '展开任务 ▾' : '收起 ▴' }}
                    </button>
                  </div>
                </div>

                <!-- 组内任务网格 -->
                <div v-if="!collapsedGroups[grp.groupKey]" class="group-task-grid">
                  <div v-for="task in grp.tasks" :key="task.task_key || task.bvid" class="task-item-card">
                    <div class="task-card-header">
                      <span class="task-type-badge" :class="{ audio: task.audio_only }">
                        {{ task.bvid.startsWith('zhihu_') ? '知乎MD+图' : task.audio_only ? 'B站音频' : `${task.quality || '视频'}` }}
                      </span>
                      <span class="task-title" :title="task.title">{{ task.title }}</span>
                      <span class="task-status-badge" :class="task.status">
                        {{ task.status === 'downloading' ? '进行中' : task.status === 'pending' ? '排队中' : task.status === 'success' ? '已完成' : task.status === 'failed' ? '失败' : task.status }}
                      </span>
                    </div>

                    <div class="task-card-meta">
                      <span class="task-author">{{ task.author }}</span>
                      <span class="task-bvid">{{ task.account_name ? `账号: ${task.account_name}` : task.bvid }}</span>
                    </div>

                    <div v-if="task.status === 'downloading' || task.status === 'pending'" class="task-progress-box">
                      <div class="task-progress-bar">
                        <div class="task-progress-fill" :style="{ width: task.progress + '%' }"></div>
                      </div>
                      <div class="task-progress-info">
                        <span>{{ task.progress }}%</span>
                        <span class="task-speed">{{ task.speed || '正在处理...' }}</span>
                      </div>
                    </div>

                    <div v-if="task.status === 'failed' && task.error" class="task-error-box">
                      <span>{{ task.error }}</span>
                    </div>

                    <div class="task-card-footer">
                      <span class="task-time" v-if="task.updated_at">{{ new Date(task.updated_at * 1000).toLocaleTimeString() }}</span>
                      <div class="task-btn-group">
                        <template v-if="task.status === 'success'">
                          <button class="btn-micro" @click="openFile(task.file_path)">▶ 打开文件</button>
                          <button class="btn-micro" @click="openFolder(task.file_path)"><AppIcon name="folder" :size="11" /> 目录</button>
                        </template>
                        <template v-else-if="task.status === 'downloading' || task.status === 'pending'">
                          <button v-if="!task.bvid.startsWith('zhihu_')" class="btn-micro" @click="handleCancelSingle(task.task_key || task.bvid)"><AppIcon name="close" :size="11" /> 取消</button>
                        </template>
                        <template v-else-if="task.status === 'failed' || task.status === 'cancelled'">
                          <button type="button" class="btn-micro retry-btn" :disabled="task.can_retry === false || task.is_stopping" @click="handleRetrySingle(task.task_key || task.bvid)">{{ task.is_stopping ? '停止中...' : '重试' }}</button>
                        </template>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 维度 B：平铺展示 -->
            <div v-else style="display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 12px;">
              <div v-for="task in filteredDrawerTasks" :key="task.task_key || task.bvid" class="task-item-card">
                <div class="task-card-header">
                  <span class="task-type-badge" :class="{ audio: task.audio_only }">
                    {{ task.bvid.startsWith('zhihu_') ? '知乎MD+图' : task.audio_only ? 'B站音频' : `${task.quality || '视频'}` }}
                  </span>
                  <span class="task-title" :title="task.title">{{ task.title }}</span>
                  <span class="task-status-badge" :class="task.status">
                    {{ task.status === 'downloading' ? '进行中' : task.status === 'pending' ? '排队中' : task.status === 'success' ? '已完成' : task.status === 'failed' ? '失败' : task.status }}
                  </span>
                </div>

                <div class="task-card-meta">
                  <span class="task-author">{{ task.author }}</span>
                  <span class="task-bvid">{{ task.account_name ? `账号: ${task.account_name}` : task.bvid }}</span>
                </div>

                <div v-if="task.status === 'downloading' || task.status === 'pending'" class="task-progress-box">
                  <div class="task-progress-bar">
                    <div class="task-progress-fill" :style="{ width: task.progress + '%' }"></div>
                  </div>
                  <div class="task-progress-info">
                    <span>{{ task.progress }}%</span>
                    <span class="task-speed">{{ task.speed || '正在处理...' }}</span>
                  </div>
                </div>

                <div v-if="task.status === 'failed' && task.error" class="task-error-box">
                  <span>{{ task.error }}</span>
                </div>

                <div class="task-card-footer">
                  <span class="task-time" v-if="task.updated_at">{{ new Date(task.updated_at * 1000).toLocaleTimeString() }}</span>
                  <div class="task-btn-group">
                    <template v-if="task.status === 'success'">
                      <button class="btn-micro" @click="openFile(task.file_path)">▶ 打开文件</button>
                      <button class="btn-micro" @click="openFolder(task.file_path)"><AppIcon name="folder" :size="11" /> 目录</button>
                    </template>
                    <template v-else-if="task.status === 'downloading' || task.status === 'pending'">
                      <button v-if="!task.bvid.startsWith('zhihu_')" class="btn-micro" @click="handleCancelSingle(task.task_key || task.bvid)"><AppIcon name="close" :size="11" /> 取消</button>
                    </template>
                    <template v-else-if="task.status === 'failed' || task.status === 'cancelled'">
                      <button type="button" class="btn-micro retry-btn" :disabled="task.can_retry === false || task.is_stopping" @click="handleRetrySingle(task.task_key || task.bvid)">{{ task.is_stopping ? '停止中...' : '重试' }}</button>
                    </template>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 分支 E：一级 Tab「系统设置」全屏主工作区 -->
        <template v-else-if="activePlatform === 'settings'">
          <div class="settings-fullscreen-view">
            <!-- 顶部工具栏：明确标注保存规则与未保存状态 -->
            <div class="settings-top-bar">
              <div class="settings-bar-left">
                <div class="settings-title-group" style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                  <h2 class="settings-main-title">{{ getSettingsTabTitle(activeSettingsTab) }}</h2>
                  <span v-if="activeSettingsTab === 'appearance'" class="save-rule-pill auto">实时保存 · 更改后立即生效</span>
                  <span v-else-if="activeSettingsTab !== 'about'" class="save-rule-pill manual" :class="{ dirty: settingsDirty || limiterDirty }">
                    {{ (settingsDirty || limiterDirty) ? '● 有未保存的修改 · 请点击右侧「保存设置」' : '手动保存 · 修改后需点击「保存设置」生效' }}
                  </span>
                </div>
              </div>
              <div class="settings-bar-right">
                <span v-if="settingsSaveNotice" class="settings-save-toast">
                  <AppIcon name="check" :size="13" />
                  <span>{{ settingsSaveNotice }}</span>
                </span>
                <button
                  v-if="activeSettingsTab !== 'appearance' && activeSettingsTab !== 'about'"
                  type="button"
                  class="btn btn-primary"
                  @click="saveAllSettings"
                >
                  <AppIcon name="save" :size="14" />
                  <span>{{ (settingsDirty || limiterDirty) ? '保存设置 *' : '保存设置' }}</span>
                </button>
              </div>
            </div>

            <!-- 主内容滚动区 -->
            <div class="settings-content-scroll">
              <div class="settings-container-max">
                <!-- 1. 下载配置 Tab -->
                <div v-if="activeSettingsTab === 'download'" class="tab-pane">
                  <!-- 并发任务数 -->
                  <div class="setting-card">
                    <div class="setting-card-header">
                      <div>
                        <h4 class="setting-title">并发下载任务数</h4>
                        <p class="setting-desc">建议设置 3～6 个并发任务。过高可能引起宽带拥堵或平台风控。</p>
                      </div>
                    </div>
                    <div class="worker-slider-row" style="margin-top: 8px;">
                      <input
                        type="range"
                        v-model.number="settingWorkers"
                        min="1"
                        max="16"
                        step="1"
                        class="slider"
                      />
                      <input
                        type="number"
                        v-model.number="settingWorkers"
                        min="1"
                        max="16"
                        class="worker-num-input"
                      />
                    </div>
                  </div>

                  <!-- 下载保存目录 -->
                  <div class="setting-card">
                    <h4 class="setting-title">下载位置</h4>
                    <p class="setting-desc">
                      所有下载内容均按平台自动进行一级目录分类：
                      <code>{{ settingDownloadDir || 'D:\download' }}\bilibili\博主名\...</code> 与
                      <code>{{ settingDownloadDir || 'D:\download' }}\zhihu\分类目录\...</code>
                    </p>
                    <div class="setting-input-row" style="margin-top: 8px;">
                      <input
                        v-model="settingDownloadDir"
                        type="text"
                        class="setting-input"
                        placeholder="例如: D:\download"
                      />
                      <button type="button" class="btn btn-primary" :disabled="isSelectingFolder" @click="handlePickDownloadDir">
                        <AppIcon name="folder" :size="13" />
                        <span>{{ isSelectingFolder ? '选择中...' : '选择位置' }}</span>
                      </button>
                      <button type="button" class="btn btn-secondary" @click="openFolder(settingDownloadDir)">
                        <AppIcon name="external" :size="13" />
                        <span>打开目录</span>
                      </button>
                    </div>
                  </div>

                  <!-- B站默认画质/音质与付费受限拦截策略 -->
                  <div class="setting-card">
                    <div class="setting-card-header" style="padding-bottom: 12px; border-bottom: 1px solid var(--border-color);">
                      <div>
                        <h4 class="setting-title">哔哩哔哩默认画质与音质</h4>
                        <p class="setting-desc">若视频源不支持所选规格，将自动降级匹配最高可用档位。</p>
                      </div>
                    </div>
                    <div class="setting-grid" style="margin-top: 12px; margin-bottom: 14px;">
                      <div class="setting-field">
                        <label class="field-label">默认画质:</label>
                        <select v-model="selectedQuality" class="setting-select">
                          <option value="MAX">最高可用 (自动)</option>
                          <option value="8K">8K 超高清</option>
                          <option value="DOLBY">杜比视界</option>
                          <option value="HDR">真彩 HDR</option>
                          <option value="4K">4K 超清</option>
                          <option value="1080P60">1080P 60帧</option>
                          <option value="1080P+">1080P+ 高码率</option>
                          <option value="1080P">1080P 高清</option>
                          <option value="720P">720P 高清</option>
                          <option value="480P">480P 清晰</option>
                          <option value="360P">360P 流畅</option>
                        </select>
                      </div>
                      <div class="setting-field">
                        <label class="field-label">默认音质:</label>
                        <select v-model="selectedAudioQuality" class="setting-select">
                          <option value="30251">Hi-Res 无损</option>
                          <option value="30250">杜比全景声</option>
                          <option value="30280">192K 高品质</option>
                          <option value="30232">132K 标准</option>
                          <option value="30216">64K 省流</option>
                        </select>
                      </div>
                    </div>
                    <div class="setting-card-header" style="padding-top: 12px; border-top: 1px solid var(--border-color);">
                      <div>
                        <h4 class="setting-title">跳过付费与受限视频</h4>
                        <p class="setting-desc">自动跳过付费课堂、充电专属及未解锁大会员的试看限制视频。</p>
                      </div>
                      <label class="toggle-switch">
                        <input type="checkbox" v-model="settingBlockPaidVideos" />
                        <span class="toggle-slider"></span>
                      </label>
                    </div>
                  </div>

                  <!-- 检查本地文件 -->
                  <div class="setting-card">
                    <div class="setting-card-header">
                      <div>
                        <h4 class="setting-title">检查本地文件</h4>
                        <p class="setting-desc">扫描下载目录，同步本地已删除或移动的文件状态。</p>
                      </div>
                      <button class="btn btn-secondary" :disabled="isReconciling" @click="handleManualReconcile">
                        <AppIcon name="search" :size="13" /> <span>{{ isReconciling ? "正在检查..." : "开始检查" }}</span>
                      </button>
                    </div>
                    <p v-if="reconcileNotice" class="reconcile-msg">{{ reconcileNotice }}</p>
                  </div>
                </div>

                <!-- 2. 刷新与下载 Tab -->
                <div v-if="activeSettingsTab === 'autocheck'" class="tab-pane">
                  <!-- 检查更新分组 -->
                  <div class="setting-card">
                    <h3 style="font-size: 15px; font-weight: 700; margin: 0 0 14px; color: var(--text-main);">检查更新</h3>

                    <!-- 1. 点击自动刷新 -->
                    <div class="setting-card-header" style="padding-bottom: 12px; border-bottom: 1px solid var(--border-color);">
                      <div>
                        <h4 class="setting-title">切换时检查更新</h4>
                        <p class="setting-desc">切换博主或收藏夹时自动检查有无新内容；关闭后仅读取本地缓存。</p>
                      </div>
                      <label class="toggle-switch">
                        <input type="checkbox" v-model="settingRefreshOnSelect" />
                        <span class="toggle-slider"></span>
                      </label>
                    </div>

                    <!-- 2. 定时刷新 -->
                    <div class="setting-card-header" style="padding-top: 12px;">
                      <div>
                        <h4 class="setting-title">后台定时刷新</h4>
                        <p class="setting-desc">后台按设定周期自动检查已启用账号下的博主与收藏夹有无新内容。</p>
                      </div>
                      <label class="toggle-switch">
                        <input type="checkbox" v-model="settingAutoCheck" />
                        <span class="toggle-slider"></span>
                      </label>
                    </div>

                    <div class="setting-grid" style="margin-top: 12px;" :class="{ 'disabled-card': !settingAutoCheck }">
                      <div class="setting-field">
                        <label class="field-label">刷新周期:</label>
                        <select v-model.number="settingCheckInterval" class="setting-select" :disabled="!settingAutoCheck">
                          <option :value="15">每 15 分钟</option>
                          <option :value="30">每 30 分钟 (推荐)</option>
                          <option :value="60">每 1 小时</option>
                          <option :value="120">每 2 小时</option>
                          <option :value="360">每 6 小时</option>
                          <option :value="720">每 12 小时</option>
                          <option :value="1440">每 24 小时 (每天一次)</option>
                        </select>
                      </div>

                      <div class="setting-field">
                        <label class="field-label">刷新范围:</label>
                        <select v-model="settingCheckScope" class="setting-select" :disabled="!settingAutoCheck">
                          <option value="special">仅特别关注博主 + 知乎监控源</option>
                          <option value="all">全部关注博主 + 知乎监控源</option>
                          <option value="all_fav">全部关注博主与收藏夹 + 知乎监控源</option>
                        </select>
                      </div>
                    </div>

                    <!-- 3. 刷新记录 -->
                    <div class="setting-card-header" style="margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border-color);">
                      <div>
                        <h4 class="setting-title">刷新记录</h4>
                        <p class="setting-desc">
                          上次刷新：<strong>{{ autoCheckStatus.last_check_time ? new Date(autoCheckStatus.last_check_time * 1000).toLocaleString() : '尚未执行' }}</strong>
                          · 累计发现：<strong>{{ autoCheckStatus.total_new_found }}</strong> 条
                          · 状态：{{ autoCheckStatus.last_message }}
                        </p>
                      </div>
                      <button
                        class="btn btn-secondary"
                        :disabled="runningManualCheck || autoCheckStatus.is_running"
                        @click="handleRunAutoCheckNow"
                      >
                        <AppIcon name="refresh" :size="13" :class="{ spinning: runningManualCheck || autoCheckStatus.is_running }" />
                        <span>{{ (runningManualCheck || autoCheckStatus.is_running) ? "正在刷新..." : "立即刷新" }}</span>
                      </button>
                    </div>
                  </div>

                  <!-- 自动下载分组 -->
                  <div class="setting-card">
                    <h3 style="font-size: 15px; font-weight: 700; margin: 0 0 14px; color: var(--text-main);">自动下载</h3>

                    <div class="setting-card-header">
                      <div>
                        <h4 class="setting-title">发现新内容时自动下载</h4>
                        <p class="setting-desc">
                          开启后，刷新发现的新内容自动加入下载队列；关闭后仅在列表展示，需手动点击下载。
                        </p>
                      </div>
                      <label class="toggle-switch">
                        <input type="checkbox" v-model="settingAutoDownload" />
                        <span class="toggle-slider"></span>
                      </label>
                    </div>
                  </div>
                </div>

                <!-- 3. 接口限流 Tab -->
                <div v-if="activeSettingsTab === 'limiter'" class="tab-pane limiter-tab-pane">
                  <!-- 平台限流切换栏 -->
                  <div class="limiter-platform-switcher">
                    <div class="segmented-control" style="width: 100%;">
                      <button
                        style="flex: 1;"
                        :class="{ active: selectedLimiterPlatform === 'bili' }"
                        @click="switchLimiterPlatform('bili')"
                      >
                        <AppIcon name="play" :size="14" />
                        <span>哔哩哔哩 (Bilibili)</span>
                      </button>
                      <button
                        style="flex: 1;"
                        :class="{ active: selectedLimiterPlatform === 'zhihu' }"
                        @click="switchLimiterPlatform('zhihu')"
                      >
                        <AppIcon name="file" :size="14" />
                        <span>知乎 (Zhihu)</span>
                      </button>
                    </div>
                  </div>

                  <!-- 监控仪表盘 -->
                  <div class="limiter-dashboard-grid">
                    <div class="metric-card">
                      <div class="metric-label">请求配额 (可用 / 容量)</div>
                      <div class="metric-value">{{ limiterStatus.available_tokens }} <span class="metric-sub">/ {{ limiterStatus.capacity }}</span></div>
                      <div class="token-water-bar">
                        <div class="token-water-fill" :style="{ width: `${Math.min(100, (limiterStatus.available_tokens / Math.max(1, limiterStatus.capacity)) * 100)}%` }"></div>
                      </div>
                    </div>

                    <div class="metric-card">
                      <div class="metric-label">队列排队等待数</div>
                      <div class="metric-value" :class="{ 'warning-text': limiterStatus.queue_size > 0 }">{{ limiterStatus.queue_size }} <span class="metric-sub">个请求</span></div>
                      <div class="metric-hint">并发请求自动入队顺序调度</div>
                    </div>

                    <div class="metric-card">
                      <div class="metric-label">请求统计 (成功 / 拦截)</div>
                      <div class="metric-value">
                        <span class="success-text">{{ limiterStatus.success_requests }}</span>
                        <span class="metric-sub"> / </span>
                        <span :class="{ 'danger-text': limiterStatus.banned_requests > 0 }">{{ limiterStatus.banned_requests }}</span>
                      </div>
                      <div class="metric-hint">总计: {{ limiterStatus.total_requests }} · 重试: {{ limiterStatus.retried_requests }}</div>
                    </div>
                  </div>

                  <!-- 熔断状态与限流调节表单 -->
                  <div class="setting-card">
                    <div class="setting-card-header" style="margin-bottom: 12px;">
                      <div>
                        <h4 class="setting-title">请求频率与高级设置</h4>
                        <p class="setting-desc">精细调节针对该平台的每秒发放令牌速率、突发容量与重试策略。</p>
                      </div>
                      <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                        <div class="segmented-control" style="margin-right: 6px;">
                          <button @click="applyPreset('safe')" title="保守模式">保守</button>
                          <button @click="applyPreset('standard')" title="标准平衡">标准</button>
                          <button @click="applyPreset('fast')" title="快速下载">快速</button>
                          <button @click="applyPreset('turbo')" title="极速突发">极速</button>
                        </div>
                        <button class="btn btn-secondary btn-sm" @click="toggleLimiterPause">
                          <AppIcon :name="limiterStatus.is_paused ? 'play' : 'pause'" :size="12" />
                          <span>{{ limiterStatus.is_paused ? "恢复令牌发放" : "临时暂停令牌" }}</span>
                        </button>
                        <button class="btn btn-secondary btn-sm" @click="resetLimiterStats">
                          <AppIcon name="refresh" :size="12" />
                          <span>重置统计</span>
                        </button>
                      </div>
                    </div>

                    <details class="diagnostics-details" style="margin-top: 14px;">
                      <summary style="font-size: 13px; font-weight: 600; color: var(--text-secondary); cursor: pointer; padding: 4px 0;">
                        高级设置：自定义令牌桶参数（点击展开）
                      </summary>
                      <div class="setting-grid" style="margin-top: 12px;">
                        <div class="setting-field">
                          <label class="field-label">发放速率 (每秒请求数):</label>
                          <input type="number" step="0.1" min="0.1" max="10" v-model.number="limiterForm.rate" class="setting-input" />
                        </div>
                        <div class="setting-field">
                          <label class="field-label">最大容量 (突发请求数):</label>
                          <input type="number" min="1" max="30" v-model.number="limiterForm.capacity" class="setting-input" />
                        </div>
                        <div class="setting-field">
                          <label class="field-label">最小间隔 (秒):</label>
                          <input type="number" step="0.1" min="0.1" max="10" v-model.number="limiterForm.min_interval" class="setting-input" />
                        </div>
                        <div class="setting-field">
                          <label class="field-label">风控冷却 (秒):</label>
                          <input type="number" min="5" max="120" v-model.number="limiterForm.ban_cooldown" class="setting-input" />
                        </div>
                        <div class="setting-field">
                          <label class="field-label">最大重试次数:</label>
                          <input type="number" min="0" max="10" v-model.number="limiterForm.max_retries" class="setting-input" />
                        </div>
                      </div>
                    </details>
                  </div>
                </div>

                <!-- 4. 外观偏好 Tab -->
                <div v-if="activeSettingsTab === 'appearance'" class="tab-pane">
                  <div class="setting-card">
                    <div class="setting-card-header">
                      <div>
                        <h4 class="setting-title">主题</h4>
                      </div>
                      <div class="segmented-control theme-switch">
                        <button :class="{ active: themeMode === 'light' }" @click="setTheme('light')">
                          <AppIcon name="light_mode" :size="13" />
                          <span>浅色</span>
                        </button>
                        <button :class="{ active: themeMode === 'dark' }" @click="setTheme('dark')">
                          <AppIcon name="dark_mode" :size="13" />
                          <span>深色</span>
                        </button>
                      </div>
                    </div>
                  </div>

                  <div class="setting-card">
                    <div class="setting-card-header">
                      <div>
                        <h4 class="setting-title">界面缩放</h4>
                      </div>
                      <div class="worker-val-tag">{{ Math.round(uiScale * 100) }}%</div>
                    </div>
                    <div class="worker-slider-row" style="margin-top: 10px;">
                      <input
                        type="range"
                        v-model.number="uiScale"
                        min="0.8"
                        max="1.4"
                        step="0.05"
                        class="slider"
                        @input="applyUiScale"
                      />
                      <button class="btn btn-secondary btn-sm" @click="resetUiScale">重置 100%</button>
                    </div>
                  </div>
                </div>

                <!-- 5. 关于与更新 Tab (品牌与版本更新合一紧凑区) -->
                <div v-if="activeSettingsTab === 'about'" class="tab-pane about-redesign-pane">
                  <!-- 品牌、版本与检查更新合一卡片 -->
                  <div class="setting-card m3-card-elevated">
                    <div class="m3-card-title-row">
                      <div class="m3-title-wrap" style="gap: 12px;">
                        <img src="/app-icon.png" alt="GatherBox Lite" style="width: 38px; height: 38px; border-radius: 50%; flex-shrink: 0; object-fit: cover;" />
                        <div>
                          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <h3 style="margin: 0; font-size: 16px; font-weight: 700; color: var(--text-main);">GatherBox Lite</h3>
                            <span class="m3-chip m3-chip-primary">v{{ updateInfo?.current_version || '0.2.0' }}</span>
                            <span class="m3-chip m3-chip-tonal">MIT License</span>
                          </div>
                          <p class="setting-desc" style="margin: 4px 0 0;">下载 B站音视频，保存知乎回答和文章</p>
                        </div>
                      </div>
                      <button
                        class="btn btn-secondary m3-btn-action"
                        :disabled="checkingUpdate"
                        @click="checkAppUpdate(true)"
                      >
                        <AppIcon name="refresh" :size="14" :class="{ spinning: checkingUpdate }" />
                        <span>{{ checkingUpdate ? "正在检查..." : "检查更新" }}</span>
                      </button>
                    </div>

                    <!-- 动态状态视窗 -->
                    <div class="m3-update-console">
                      <!-- 加载态 -->
                      <div v-if="checkingUpdate" class="m3-console-state checking">
                        <div class="m3-loading-spinner"></div>
                        <div class="state-msg-group">
                          <span class="state-title">正在检查更新...</span>
                        </div>
                      </div>

                      <!-- 发现新版本 -->
                      <div v-else-if="updateInfo && updateInfo.has_update" class="m3-console-state update-available">
                        <div class="update-pulse-header">
                          <div class="update-tag-pill">
                            <span class="status-pulse-dot"></span>
                            <span>新版本可用：v{{ updateInfo.latest_version }}</span>
                          </div>
                          <span v-if="updateInfo.published_at" class="update-meta-text">发布时间: {{ updateInfo.published_at.slice(0, 10) }}</span>
                        </div>
                        <div class="update-info-body">
                          <h5 class="update-headline">{{ updateInfo.release_name || 'GatherBox Lite 最新版本' }}</h5>
                          <div v-if="updateInfo.asset_name" class="update-matched-pkg">
                            已自动匹配当前系统安装包：<strong>{{ updateInfo.asset_name }}</strong>
                            <span v-if="updateInfo.asset_size"> ({{ formatUpdateBytes(updateInfo.asset_size) }})</span>
                          </div>
                          <div v-if="updateInfo.changelog" class="m3-changelog-terminal">
                            <div class="terminal-bar">
                              <span class="terminal-dot"></span>
                              <span class="terminal-title">发布说明</span>
                            </div>
                            <pre class="terminal-text">{{ updateInfo.changelog }}</pre>
                          </div>

                          <!-- 软件内下载进度条 -->
                          <div v-if="updateDownloadState.status === 'downloading' || updateDownloadState.status === 'completed'" class="update-dl-progress-box">
                            <div class="update-dl-progress-top">
                              <span>{{ updateDownloadState.status === 'completed' ? '更新包下载完成' : `正在下载 ${updateDownloadState.file_name}...` }}</span>
                              <span>
                                {{ updateDownloadState.progress }}%
                                <template v-if="updateDownloadState.total_bytes > 0">
                                  ({{ formatUpdateBytes(updateDownloadState.downloaded_bytes) }} / {{ formatUpdateBytes(updateDownloadState.total_bytes) }})
                                </template>
                                · {{ updateDownloadState.speed_text }}
                              </span>
                            </div>
                            <div class="update-dl-track">
                              <div class="update-dl-fill" :style="{ width: updateDownloadState.progress + '%' }"></div>
                            </div>
                          </div>

                          <div v-if="updateNoticeMsg" class="update-notice-tip">{{ updateNoticeMsg }}</div>
                          <div v-if="updateError" class="update-error-tip">{{ updateError }}</div>

                          <div class="update-buttons-bar">
                            <button
                              v-if="updateDownloadState.status !== 'completed'"
                              class="btn btn-primary m3-btn-download"
                              :disabled="updateDownloadState.status === 'downloading'"
                              @click="startInAppUpdateDownload"
                            >
                              <AppIcon name="download" :size="14" />
                              <span>{{ updateDownloadState.status === 'downloading' ? `下载中 (${updateDownloadState.progress}%)...` : '下载并安装更新' }}</span>
                            </button>
                            <button
                              v-if="updateDownloadState.status === 'completed'"
                              class="btn btn-primary m3-btn-download"
                              @click="triggerInstallUpdate(false)"
                            >
                              <AppIcon name="check_circle" :size="14" />
                              <span>立即运行安装程序</span>
                            </button>
                            <button
                              v-if="updateDownloadState.status === 'completed'"
                              class="btn btn-secondary"
                              @click="triggerInstallUpdate(true)"
                            >
                              <AppIcon name="folder_open" :size="14" />
                              <span>打开所在文件夹</span>
                            </button>
                            <button
                              class="btn btn-secondary"
                              @click="openExternalUrl(updateInfo.html_url || updateInfo.download_url || 'https://github.com/joelovechen/GatherBox_Lite/releases')"
                            >
                              <AppIcon name="open_in_new" :size="14" />
                              <span>打开下载页面</span>
                            </button>
                          </div>
                        </div>
                      </div>

                      <!-- 已是最新版本 -->
                      <div v-else-if="updateInfo && !updateInfo.has_update && updateInfo.success" class="m3-console-state up-to-date">
                        <div class="uptodate-badge-row">
                          <div class="uptodate-icon-box"><AppIcon name="check_circle" :size="20" /></div>
                          <div>
                            <div class="uptodate-title">已是最新版本 (v{{ updateInfo.current_version || '0.1.0' }})</div>
                          </div>
                        </div>
                      </div>

                      <!-- 未检查或出错 -->
                      <div v-else class="m3-console-state standby">
                        <div class="standby-row">
                          <div class="standby-icon"><AppIcon name="info" :size="18" /></div>
                          <div>
                            <div class="standby-title">当前版本：v0.1.0</div>
                            <div class="standby-desc">{{ updateError || '点击「检查更新」获取最新版本' }}</div>
                          </div>
                        </div>
                      </div>
                    </div>

                    <!-- 偏好设置小条 -->
                    <div class="m3-toggle-row">
                      <div class="toggle-text">
                        <span class="toggle-title">启动时自动检查更新</span>
                        <span class="toggle-desc">有更新时在侧边栏徽标提醒，不弹窗打扰</span>
                      </div>
                      <label class="toggle-switch">
                        <input type="checkbox" v-model="settingAutoCheckUpdate" @change="saveAllSettings" />
                        <span class="toggle-slider"></span>
                      </label>
                    </div>
                  </div>

                  <!-- 矩阵生态卡片 (精选 4 格卡片体系) -->
                  <div class="m3-cards-grid">
                    <div class="m3-interactive-card" @click="openExternalUrl('https://github.com/joelovechen/GatherBox_Lite')">
                      <div class="card-icon-halo"><AppIcon name="code" :size="18" /></div>
                      <div class="card-text-body">
                        <div class="card-headline-row">
                          <h5 class="card-headline">开源核心仓库</h5>
                          <AppIcon name="open_in_new" :size="13" class="external-glyph" />
                        </div>
                        <p class="card-caption">访问 GitHub 源码库、参与 Star 或查看提交历史</p>
                        <span class="card-sub-code">joelovechen / GatherBox_Lite</span>
                      </div>
                    </div>

                    <div class="m3-interactive-card" @click="openExternalUrl('https://github.com/joelovechen')">
                      <div class="card-icon-halo"><AppIcon name="person" :size="18" /></div>
                      <div class="card-text-body">
                        <div class="card-headline-row">
                          <h5 class="card-headline">作者主页</h5>
                          <AppIcon name="open_in_new" :size="13" class="external-glyph" />
                        </div>
                        <p class="card-caption">了解作者全栈技术栈、设计理念与其它开源工具</p>
                        <span class="card-sub-code">github.com / joelovechen</span>
                      </div>
                    </div>

                    <div class="m3-interactive-card" @click="openExternalUrl('https://github.com/joelovechen/GatherBox_Lite/issues')">
                      <div class="card-icon-halo"><AppIcon name="info" :size="18" /></div>
                      <div class="card-text-body">
                        <div class="card-headline-row">
                          <h5 class="card-headline">问题反馈</h5>
                          <AppIcon name="open_in_new" :size="13" class="external-glyph" />
                        </div>
                        <p class="card-caption">遇到平台解析异常或有新功能构想？欢迎直连提报</p>
                        <span class="card-sub-code">GatherBox_Lite / issues</span>
                      </div>
                    </div>

                    <div class="m3-interactive-card plain">
                      <div class="card-icon-halo emerald"><AppIcon name="security" :size="18" /></div>
                      <div class="card-text-body">
                        <div class="card-headline-row">
                          <h5 class="card-headline">本地纯粹安全承诺</h5>
                          <span class="chip-status-safe">100% 本地</span>
                        </div>
                        <p class="card-caption">所有凭据与归档数据仅保存在本机 SQLite，零云端遥测采集</p>
                        <span class="card-sub-code" style="color: var(--sys-accent-green);">SQLite 3.45 · No External Analytics</span>
                      </div>
                    </div>
                  </div>

                  <!-- 运行环境与诊断信息（折叠） -->
                  <details class="setting-card diagnostics-details">
                    <summary class="m3-card-title-row" style="cursor: pointer; list-style: none;">
                      <div class="m3-title-wrap">
                        <div class="m3-section-icon"><AppIcon name="tune" :size="18" /></div>
                        <div>
                          <h4 class="setting-title" style="margin: 0; font-size: 15px;">诊断信息</h4>
                          <p class="setting-desc" style="margin: 2px 0 0;">运行时与本地数据统计（点击展开）</p>
                        </div>
                      </div>
                    </summary>

                    <div class="m3-specs-grid" style="margin-top: 14px;">
                      <div class="m3-spec-item">
                        <span class="spec-prop">Native 框架</span>
                        <span class="spec-val">Tauri 2.0 (Rust) · WebView2 · Vue 3</span>
                      </div>
                      <div class="m3-spec-item">
                        <span class="spec-prop">API 服务</span>
                        <span class="spec-val">Python 3.11 · FastAPI · Uvicorn</span>
                      </div>
                      <div class="m3-spec-item">
                        <span class="spec-prop">B站内容</span>
                        <span class="spec-val highlight-bili">{{ bloggers.length }} 位 UP主 · {{ videos.length }} 条视频</span>
                      </div>
                      <div class="m3-spec-item">
                        <span class="spec-prop">知乎内容</span>
                        <span class="spec-val highlight-zhihu">{{ zhihuCreators.length }} 位创作者 · {{ zhihuContents.length }} 篇内容</span>
                      </div>
                    </div>
                  </details>
                </div></div>
            </div>
          </div>
        </template>
</main>

      <!-- ==================== 第 4 栏：右侧沉浸式阅读预览 & 跨平台归档队列面板 ==================== -->
      <aside v-if="showRightPanel && activePlatform !== 'tasks' && activePlatform !== 'accounts' && activePlatform !== 'settings'" class="right-detail-panel">
        <div class="right-panel-tabs">
          <button
            class="right-tab-btn"
            :class="{ active: rightPanelTab === 'reader' }"
            @click="rightPanelTab = 'reader'"
          >
            <span style="display:inline-flex;align-items:center;gap:4px;"><AppIcon name="book" :size="13" />内容预览 / 阅读器</span>
          </button>
          <button
            class="right-tab-btn"
            :class="{ active: rightPanelTab === 'queue' }"
            @click="rightPanelTab = 'queue'"
          >
            <span style="display:inline-flex;align-items:center;gap:4px;"><AppIcon name="inbox" :size="13" />队列与日志 ({{ activeDownloadCount || allTasks.length }})</span>
          </button>
        </div>

        <!-- 右侧视图 1：沉浸式内容阅读器 -->
        <div v-if="rightPanelTab === 'reader'" class="right-reader-body">
          <template v-if="activeReaderItem">
            <div class="reader-meta-box">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                <span class="zhihu-type-tag" :class="activeReaderItem.content_type">
                  {{ activeReaderItem.content_type === 'article' ? '知乎专栏文章' : '知乎回答' }}
                </span>
                <span v-if="activeReaderItem.download_status === 'success'" style="font-size: 11px; color: var(--sys-accent-green); font-weight: 600;">
                  <AppIcon name="check" :size="12" /> 已本地化归档 (MD + HTML + 配图)
                </span>
              </div>
              <h3 class="reader-title">{{ activeReaderItem.title }}</h3>
              <div class="reader-author-line">
                <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="person" :size="12" />{{ activeReaderItem.author_name }}</span>
                <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="thumb_up" :size="12" />{{ activeReaderItem.voteup_count }} 赞同</span>
                <span style="display:inline-flex;align-items:center;gap:3px;"><AppIcon name="chat" :size="12" />{{ activeReaderItem.comment_count }} 评论</span>
              </div>
              <div class="reader-action-row">
                <template v-if="activeReaderItem.is_paid === 1 || activeReaderItem.is_restricted">
                  <button class="btn-micro disabled-paid" disabled :title="activeReaderItem.paid_reason || activeReaderItem.restrict_reason || '知乎盐选/付费专栏内容，受版权保护已禁止爬取'">
                    <AppIcon name="lock" :size="12" /> <span>盐选受限 (禁止归档)</span>
                  </button>
                </template>
                <button v-else class="btn-micro primary" @click="handleArchiveZhihuSingle(activeReaderItem)">
                  <AppIcon name="save" :size="12" /> <span>{{ activeReaderItem.download_status === 'success' ? '重新归档' : '归档本地' }}</span>
                </button>
                <button v-if="activeReaderItem.local_file" class="btn-micro" @click="openFile(activeReaderItem.local_file)">
                  <AppIcon name="file" :size="12" /> <span>打开 .md</span>
                </button>
                <button v-if="activeReaderItem.local_file" class="btn-micro" @click="openFolder(activeReaderItem.local_file)">
                  <AppIcon name="folder" :size="12" /> <span>所在目录</span>
                </button>
                <button class="btn-micro" @click="openInBrowser(activeReaderItem.content_url)">
                  ↗ 原网页
                </button>
              </div>
            </div>

            <div v-if="loadingReaderDetail" style="padding: 24px; text-align: center; color: var(--text-muted); font-size: 12px;">
              <AppIcon name="clock" :size="12" class="spinning" /> 正在拉取完整富文本正文与配图...
            </div>
            <div
              v-else
              class="reader-rich-content"
              v-html="localizedReaderHtml || `<p>${activeReaderItem.excerpt || '暂无正文内容'}</p>`"
            ></div>
          </template>
          <div v-else class="empty-state" style="padding: 40px 20px;">
            <p style="font-size: 13px;">点击中间列表的任意知乎回答或专栏文章，即可在此处直接沉浸式阅读完整图文并一键归档为本地 Markdown。</p>
          </div>
        </div>

        <!-- 右侧视图 2：跨平台归档队列与实时签名/巡检日志 -->
        <div v-else class="right-queue-body">
          <div class="right-queue-list">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
              <span style="font-size: 12px; font-weight: 600; color: var(--text-muted);">
                正在执行 / 最近归档任务 ({{ allTasks.length }})
              </span>
              <button v-if="successCount > 0" class="btn-micro" @click="handleClearCompleted"><AppIcon name="trash" :size="11" /> 清理完成</button>
            </div>

            <div v-if="allTasks.length === 0" style="padding: 24px 0; text-align: center; color: var(--text-muted); font-size: 12px;">
              当前队列空闲
            </div>

            <div v-for="task in allTasks.slice(0, 25)" :key="task.task_key || task.bvid" class="right-task-card">
              <div style="display: flex; justify-content: space-between; gap: 6px; font-size: 12px; font-weight: 600;">
                <span class="truncate">{{ task.bvid.startsWith('zhihu_') ? '知乎' : 'B站' }} {{ task.title }}</span>
                <span :style="{ color: task.status === 'success' ? 'var(--sys-accent-green)' : task.status === 'failed' ? 'var(--sys-accent-red)' : 'var(--sys-accent-blue)', flexShrink: 0 }">
                  {{ task.status === 'success' ? '已完成' : task.status === 'failed' ? '失败' : `${task.progress}%` }}
                </span>
              </div>
              <div style="font-size: 11px; color: var(--text-muted); display: flex; justify-content: space-between; margin-top: 4px;">
                <span>{{ task.author }} · {{ task.speed }}</span>
                <div v-if="task.status === 'success' && task.file_path" style="display: flex; gap: 6px;">
                  <button class="btn-micro" @click="openFile(task.file_path)">打开</button>
                  <button class="btn-micro" @click="openFolder(task.file_path)">目录</button>
                </div>
              </div>
            </div>
          </div>

          <!-- 底部实时签名与请求日志 -->
          <div class="right-audit-logs">
            <div style="font-size: 11px; font-weight: 600; margin-bottom: 6px; color: var(--text-main);">
              实时请求与签名审计 ({{ limiterStatus.config.rate }} QPS)
            </div>
            <div v-if="limiterStatus.recent_logs.length === 0" style="color: var(--text-muted);">
              等待请求触发...
            </div>
            <div v-for="log in limiterStatus.recent_logs.slice(0, 12)" :key="log.id" class="audit-log-line">
              <span>[{{ log.time }}]</span>
              <span :style="{ color: log.status_code === 200 ? 'var(--sys-accent-green)' : 'var(--sys-accent-red)' }">{{ log.status_code }}</span>
              <span class="truncate" :title="log.url">{{ log.url.replace('https://www.zhihu.com', '').replace('https://api.bilibili.com', '') }}</span>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <!-- 知乎扫码 / 浏览器过验证 / Cookie 登录模态框 -->
    <div v-if="showZhihuLoginModal" class="modal-backdrop" @click.self="closeZhihuLoginModal">
      <div class="modal-card" role="dialog" aria-modal="true" aria-label="知乎账号登录" tabindex="-1" style="max-width: 520px;">
        <div class="modal-header">
          <h3>知乎账号登录</h3>
          <button class="btn-close" @click="closeZhihuLoginModal"><AppIcon name="close" :size="14" /></button>
        </div>

        <div class="settings-tab-nav" style="padding: 0 20px;">
          <button class="tab-nav-btn" :class="{ active: zhihuLoginTab === 'qrcode' }" @click="changeZhihuLoginTab('qrcode')">
            <AppIcon name="qr_code" :size="13" /> <span>扫码登录</span>
          </button>
          <button class="tab-nav-btn" :class="{ active: zhihuLoginTab === 'browser' }" @click="changeZhihuLoginTab('browser')">
            <AppIcon name="globe" :size="13" /> <span>浏览器窗口</span>
          </button>
          <button class="tab-nav-btn" :class="{ active: zhihuLoginTab === 'cookie' }" @click="changeZhihuLoginTab('cookie')">
            <AppIcon name="key" :size="13" /> <span>Cookie 导入</span>
          </button>
        </div>

        <div v-if="zhihuLoginTab === 'qrcode'" class="modal-body qr-body">
          <div class="qr-wrapper">
            <img v-if="zhihuQrImg" :src="zhihuQrImg" class="qr-img" />
            <div v-else class="spinner"></div>
          </div>
          <p class="qr-status" :style="{ color: zhihuQrBlocked ? 'var(--sys-accent-orange)' : '' }">{{ zhihuQrStatus }}</p>
          <span class="qr-tip">请使用手机「知乎 App」右上角扫一扫登录；若触发 40352 安全验证，请点击下方「浏览器窗口扫码/过验证」</span>
          <div style="margin-top: 12px; display: flex; gap: 8px; justify-content: center; flex-wrap: wrap;">
            <button class="btn btn-secondary" @click="startZhihuQrFlow">刷新二维码</button>
            <button class="btn btn-primary" style="background: var(--sys-accent-blue);" @click="handleStartZhihuBrowserLogin">
              <AppIcon name="globe" :size="13" /><span>浏览器窗口扫码/过验证</span>
            </button>
            <button v-if="zhihuUser.is_login" class="btn btn-outline" @click="handleZhihuLogout">退出知乎登录</button>
          </div>
        </div>

        <div v-else-if="zhihuLoginTab === 'browser'" class="modal-body" style="padding: 24px; text-align: center;">
          <div style="display:flex;justify-content:center;margin-bottom:10px;"><AppIcon name="globe" :size="38" /></div>
          <p style="font-size: 14px; font-weight: 600; color: var(--text-main); margin-bottom: 10px;">
            {{ zhihuQrStatus || "启动本地 Edge / Chrome 浏览器窗口完成知乎验证与扫码" }}
          </p>
          <p style="font-size: 12px; color: var(--text-muted); line-height: 1.6; margin-bottom: 16px;">
            当知乎接口触发 <code>40352 (unhuman)</code> 安全验证时，点击下方按钮将弹出一个真实浏览器窗口打开 <code>zhihu.com/signin</code>。<br/>
            您只需在弹出的浏览器窗口中点击「开始验证」并扫码登录，程序会自动提取 <code>z_c0</code> 与 <code>d_c0</code> 并关闭窗口。
          </p>
          <button class="btn btn-primary" style="background: var(--sys-accent-blue);" :disabled="zhihuBrowserLoggingIn" @click="handleStartZhihuBrowserLogin">
            {{ zhihuBrowserLoggingIn ? "等待浏览器窗口完成登录..." : "弹出浏览器窗口登录知乎" }}
          </button>
        </div>

        <div v-else class="modal-body">
          <p class="input-tip">
            在浏览器打开 <code>www.zhihu.com</code> 并登录，按 F12 打开网络面板复制任意请求的 Cookie 字符串（或仅粘贴 <code>z_c0=...</code>）：
          </p>
          <textarea
            v-model="zhihuCookieInput"
            class="cookie-textarea"
            placeholder="粘贴包含 z_c0=...; d_c0=... 的知乎 Cookie 字符串"
            rows="5"
          ></textarea>
          <p v-if="zhihuCookieError" class="error-text">{{ zhihuCookieError }}</p>
          <div style="margin-top: 12px; display: flex; justify-content: flex-end; gap: 8px;">
            <button class="btn btn-primary" :disabled="zhihuCookieLoading" @click="handleImportZhihuCookie">
              {{ zhihuCookieLoading ? "验证中..." : "验证并保存知乎凭证" }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Electron 官方独立窗口登录模态控制卡片 -->
    <div v-if="showElectronLoginModal" class="modal-backdrop" @click.self="handleCloseElectronLogin">
      <div class="modal-card" role="dialog" aria-modal="true" aria-label="哔哩哔哩官方登录" tabindex="-1" style="max-width: 500px;">
        <div class="modal-header">
          <h3>哔哩哔哩官方登录</h3>
          <button class="btn-close" @click="handleCloseElectronLogin"><AppIcon name="close" :size="14" /></button>
        </div>
        <div class="modal-body" style="padding: 24px; text-align: center;">
          <div style="display:flex;justify-content:center;margin-bottom:12px;"><AppIcon name="key" :size="40" /></div>
          <p style="font-size: 14px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 16px;">
            {{ electronLoginStatus }}
          </p>
          <div style="font-size: 12px; color: var(--text-muted); background: var(--bg-hover); padding: 12px; border-radius: 8px; text-align: left; line-height: 1.5;">
            • 已在受保护的隔离窗口中打开官方登录页。<br/>
            • 您可使用 B站 App 扫码、验证码或密码登录，软件不拦截也不收集您的密码。<br/>
            • 成功后系统自动识别并同步 Session。若未自动识别，可点击下方手动校验。
          </div>
        </div>
        <div class="modal-footer" style="display: flex; justify-content: space-between;">
          <button class="btn btn-outline" @click="handleCloseElectronLogin">取消</button>
          <button class="btn btn-primary" @click="handleManualVerifyLogin"><AppIcon name="check" :size="13" /> <span>完成登录 (立即校验)</span></button>
        </div>
      </div>
    </div>

    <!-- 哔哩哔哩账号登录弹窗 (扫码与 Cookie 标签页统一) -->
    <div v-if="showQrModal || showCookieModal" class="modal-backdrop" @click.self="closeQrModal(); showCookieModal = false">
      <div class="modal-card" role="dialog" aria-modal="true" aria-label="添加哔哩哔哩账号" tabindex="-1" style="max-width: 480px;">
        <div class="modal-header">
          <h3>添加哔哩哔哩账号</h3>
          <button class="btn-close" @click="closeQrModal(); showCookieModal = false"><AppIcon name="close" :size="14" /></button>
        </div>

        <div class="modal-tabs" style="display: flex; gap: 8px; padding: 0 20px 10px; border-bottom: 1px solid var(--border-color);">
          <button
            class="modal-tab-btn"
            :class="{ active: (biliLoginTab === 'qrcode' && !showCookieModal) }"
            @click="biliLoginTab = 'qrcode'; showCookieModal = false; showQrModal = true; if (!qrImg) openQrLogin()"
          >
            App 扫码登录
          </button>
          <button
            class="modal-tab-btn"
            :class="{ active: (biliLoginTab === 'cookie' || showCookieModal) }"
            @click="biliLoginTab = 'cookie'; showQrModal = false; showCookieModal = true"
          >
            导入 Cookie
          </button>
        </div>

        <!-- 扫码登录内容 -->
        <template v-if="biliLoginTab === 'qrcode' && !showCookieModal">
          <div class="modal-body qr-body">
            <div class="qr-wrapper">
              <img v-if="qrImg" :src="qrImg" class="qr-img" />
              <div v-else class="spinner"></div>
            </div>
            <p class="qr-status">{{ qrStatusText }}</p>
            <span class="qr-tip">使用哔哩哔哩客户端扫码确认登录</span>
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary" @click="openQrLogin">刷新二维码</button>
            <button v-if="user.is_login" class="btn btn-outline" @click="handleLogout">退出当前登录</button>
            <button class="btn btn-outline" @click="closeQrModal">取消</button>
          </div>
        </template>

        <!-- 导入 Cookie 内容 -->
        <template v-else>
          <div class="modal-body">
            <p class="input-tip">在浏览器登录 B站 后，在开发者工具 (F12) 的请求头中复制 Cookie 字符串：</p>
            <textarea
              v-model="cookieInput"
              class="cookie-textarea"
              placeholder="必须包含 SESSDATA=...; bili_jct=... 等核心字段"
              rows="6"
            ></textarea>
            <p v-if="cookieError" class="error-text">{{ cookieError }}</p>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline" @click="showCookieModal = false">取消</button>
            <button class="btn btn-primary" :disabled="cookieLoading" @click="handleImportCookie">
              {{ cookieLoading ? "校验中..." : "校验并保存" }}
            </button>
          </div>
        </template>
      </div>
    </div>

    <!-- 全局任务队列抽屉 Drawer -->
    <div v-if="showTaskDrawer" class="drawer-backdrop" @click.self="showTaskDrawer = false">
      <div class="drawer-panel" role="dialog" aria-modal="true" aria-label="下载任务队列" tabindex="-1">
        <div class="drawer-header">
          <div class="drawer-title-group">
            <h3>下载任务队列</h3>
            <span class="task-summary">{{ runningCount }} 进行中 · {{ successCount }} 完成 · {{ failedCount }} 失败</span>
          </div>
          <button class="btn-close" @click="showTaskDrawer = false"><AppIcon name="close" :size="14" /></button>
        </div>

        <!-- 快捷全局操作栏 -->
        <div class="drawer-actions-bar">
          <button class="btn btn-drawer-tool" :disabled="isReconciling" @click="handleManualReconcile" title="扫描磁盘同步已下载/已删除的文件">
            <AppIcon name="refresh" :size="12" :class="{ spinning: isReconciling }" />
            {{ isReconciling ? "正在检查..." : "检查本地文件" }}
          </button>
          <button v-if="failedCount > 0" class="btn btn-drawer-tool danger" @click="handleRetryAllFailed" title="重新下载所有失败项目">
            重试全部失败 ({{ failedCount }})
          </button>
          <button v-if="successCount > 0" class="btn btn-drawer-tool" @click="handleClearCompleted" title="清空已完成记录">
            <AppIcon name="trash" :size="12" /><span>清空已完成</span>
          </button>
        </div>

        <!-- 对账结果提示条 -->
        <div v-if="reconcileNotice" class="reconcile-alert">
          {{ reconcileNotice }}
        </div>

        <!-- 状态筛选 Tabs -->
        <div class="drawer-tabs">
          <button :class="{ active: drawerTab === 'all' }" @click="drawerTab = 'all'">全部 ({{ allTasks.length }})</button>
          <button :class="{ active: drawerTab === 'running' }" @click="drawerTab = 'running'">进行中 ({{ runningCount }})</button>
          <button :class="{ active: drawerTab === 'success' }" @click="drawerTab = 'success'">已完成 ({{ successCount }})</button>
          <button :class="{ active: drawerTab === 'failed' }" @click="drawerTab = 'failed'">失败 ({{ failedCount }})</button>
        </div>

        <!-- 任务滚动列表 -->
        <div class="drawer-task-list">
          <div v-if="filteredDrawerTasks.length === 0" class="drawer-empty">
            <p>暂无相关任务</p>
          </div>
          <div v-for="task in filteredDrawerTasks" :key="task.bvid" class="task-item-card">
            <div class="task-card-header">
              <span class="task-type-badge" :class="{ audio: task.audio_only }">
                {{ task.audio_only ? "音频 AAC" : task.quality }}
              </span>
              <span class="task-title" :title="task.title">{{ task.title }}</span>
              <span class="task-status-badge" :class="task.status">
                {{ task.status === 'downloading' ? '下载中' : task.status === 'pending' ? '排队中' : task.status === 'success' ? '完成' : task.status === 'failed' ? '失败' : task.status }}
              </span>
            </div>

            <div class="task-card-meta">
              <span class="task-author">{{ task.author }}</span>
              <span class="task-bvid">{{ task.bvid }}</span>
            </div>

            <!-- 下载进度条 -->
            <div v-if="task.status === 'downloading' || task.status === 'pending'" class="task-progress-box">
              <div class="task-progress-bar">
                <div class="task-progress-fill" :style="{ width: task.progress + '%' }"></div>
              </div>
              <div class="task-progress-info">
                <span>{{ task.progress }}%</span>
                <span class="task-speed">{{ task.speed || '正在建立连接...' }}</span>
              </div>
            </div>

            <!-- 错误提示 -->
            <div v-if="task.status === 'failed' && task.error" class="task-error-box">
              <span>{{ task.error }} (已重试: {{ task.retries }}/{{ task.max_retries }})</span>
            </div>

            <!-- 操作动作条 -->
            <div class="task-card-footer">
              <span class="task-time" v-if="task.updated_at">{{ new Date(task.updated_at * 1000).toLocaleTimeString() }}</span>
              <div class="task-btn-group">
                <template v-if="task.status === 'success'">
                  <button class="btn-micro" @click="openFile(task.file_path)" title="播放本地文件">▶ 播放</button>
                  <button class="btn-micro" @click="openFolder(task.file_path)" title="在文件夹中定位"><AppIcon name="folder" :size="12" /><span>打开位置</span></button>
                </template>
                <template v-else-if="task.status === 'downloading' || task.status === 'pending'">
                  <button class="btn-micro" @click="handleCancelSingle(task.bvid)" title="中断并取消该下载任务"><AppIcon name="close" :size="12" /><span>取消下载</span></button>
                </template>
                <template v-else-if="task.status === 'failed' || task.status === 'cancelled'">
                  <button class="btn-micro retry-btn" @click="handleRetrySingle(task.bvid)">重试任务</button>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 全局系统统一设置中心 -->

    <!-- 免责声明与使用协议模态弹窗 (强制首次阅读并确认) -->
    <div v-if="showDisclaimerModal" class="disclaimer-modal-overlay">
      <div class="disclaimer-modal-card" role="dialog" aria-modal="true" aria-label="软件使用协议与免责声明" tabindex="-1">
        <div class="disclaimer-modal-header">
          <div class="disclaimer-title-row">
            <span class="disclaimer-icon">⚖️</span>
            <div>
              <h3>GatherBox 软件使用协议与免责声明</h3>
              <p class="disclaimer-subtitle">首次启动前请仔细阅读以下条款并确认遵守</p>
            </div>
          </div>
        </div>

        <div class="disclaimer-modal-body">
          <div class="disclaimer-section">
            <h4>一、开源学习与个人非商业用途</h4>
            <p>
              GatherBox 是一款面向个人开发者与研究者的开源离线知识库归档工具。本项目及其衍生软件仅供<strong>计算机网络通信、爬虫工程逆向、多协议数据归档以及个人合法收藏学习</strong>之用，严禁任何个人或组织将其用于商业牟利、批量转售、黑灰产分发或非法传播。
            </p>
          </div>

          <div class="disclaimer-section warning-box">
            <h4>二、知识产权保护与平台付费内容硬限制（重点须知）</h4>
            <p>
              本软件坚决维护创作者知识产权与平台合法商业权益。系统已在<strong>核心底层与路由分发层全面内置技术拦截限制</strong>，严格杜绝以下受限内容的爬取与下载：
            </p>
            <ul>
              <li><strong>哔哩哔哩 (Bilibili)</strong>：严禁爬取任何平台付费视频、充电专属投稿、大会员试看限制视频（仅提供未登录试看片段之内容直接拒绝下载）；</li>
              <li><strong>知乎 (Zhihu)</strong>：严禁爬取任何知乎盐选专栏 (Yanxuan)、付费咨询、Kvip 专栏、付费文章等所有平台付费受保护内容。系统检测到盐选/受限标记将直接拦截并禁用归档。</li>
            </ul>
          </div>

          <div class="disclaimer-section">
            <h4>三、合规使用与免责声明</h4>
            <p>
              1. 使用者在使用本软件访问各第三方平台时，须严格遵守中华人民共和国相关法律法规以及相关平台的用户服务协议，合理控制抓取频率与请求并发，不得对平台服务器造成不当压力。<br/>
              2. 任何因违反平台协议、侵犯知识产权、擅自分发下载内容或商业滥用所导致的法律风险与纠纷，均由使用者本人独立承担，与本开源项目的开发者及维护者无关。<br/>
              3. 如您不同意上述条款，请点击“拒绝并退出”，软件将立即安全关闭。
            </p>
          </div>
        </div>

        <div class="disclaimer-modal-footer">
          <button class="btn btn-outline disclaimer-btn-reject" @click="handleRejectDisclaimer">
            拒绝并退出
          </button>
          <button class="btn btn-primary disclaimer-btn-accept" @click="handleAcceptDisclaimer">
            我已阅读并同意上述声明
          </button>
        </div>
      </div>
    </div>

    <!-- 启动自动检查更新弹窗 -->
    <div v-if="showUpdateModal && updateInfo && updateInfo.has_update && !showDisclaimerModal" class="disclaimer-modal-overlay" @click.self="showUpdateModal = false">
      <div class="disclaimer-modal-card update-modal-card" role="dialog" aria-modal="true" aria-label="发现新版本" tabindex="-1">
        <div class="disclaimer-modal-header">
          <div class="disclaimer-title-row">
            <span class="disclaimer-icon">🚀</span>
            <div>
              <h3>发现新版本 v{{ updateInfo.latest_version }}</h3>
              <p class="disclaimer-subtitle">
                当前版本 v{{ updateInfo.current_version }}
                <template v-if="updateInfo.published_at"> · 发布于 {{ updateInfo.published_at.slice(0, 10) }}</template>
              </p>
            </div>
          </div>
        </div>

        <div class="disclaimer-modal-body">
          <div v-if="updateInfo.asset_name" class="update-matched-pkg">
            已为您自动匹配当前系统安装包：<strong>{{ updateInfo.asset_name }}</strong>
            <span v-if="updateInfo.asset_size"> ({{ formatUpdateBytes(updateInfo.asset_size) }})</span>
          </div>

          <div v-if="updateInfo.changelog" class="disclaimer-section">
            <h4>更新说明</h4>
            <pre class="update-modal-changelog">{{ updateInfo.changelog }}</pre>
          </div>

          <div v-if="updateDownloadState.status === 'downloading' || updateDownloadState.status === 'completed'" class="update-dl-progress-box">
            <div class="update-dl-progress-top">
              <span>{{ updateDownloadState.status === 'completed' ? '更新包已下载完成' : `正在下载 ${updateDownloadState.file_name}...` }}</span>
              <span>
                {{ updateDownloadState.progress }}%
                <template v-if="updateDownloadState.total_bytes > 0">
                  ({{ formatUpdateBytes(updateDownloadState.downloaded_bytes) }} / {{ formatUpdateBytes(updateDownloadState.total_bytes) }})
                </template>
                · {{ updateDownloadState.speed_text }}
              </span>
            </div>
            <div class="update-dl-track">
              <div class="update-dl-fill" :style="{ width: updateDownloadState.progress + '%' }"></div>
            </div>
          </div>

          <div v-if="updateNoticeMsg" class="update-notice-tip">{{ updateNoticeMsg }}</div>
          <div v-if="updateError" class="update-error-tip">{{ updateError }}</div>
        </div>

        <div class="disclaimer-modal-footer update-modal-footer">
          <button class="btn btn-outline" @click="showUpdateModal = false">
            稍后更新
          </button>
          <button
            class="btn btn-secondary"
            @click="openExternalUrl(updateInfo.html_url || updateInfo.download_url || 'https://github.com/joelovechen/GatherBox_Lite/releases')"
          >
            打开下载页面
          </button>
          <button
            v-if="updateDownloadState.status === 'completed'"
            class="btn btn-secondary"
            @click="triggerInstallUpdate(true)"
          >
            打开所在文件夹
          </button>
          <button
            v-if="updateDownloadState.status === 'completed'"
            class="btn btn-primary disclaimer-btn-accept"
            @click="triggerInstallUpdate(false)"
          >
            立即运行安装程序
          </button>
          <button
            v-else
            class="btn btn-primary disclaimer-btn-accept"
            :disabled="updateDownloadState.status === 'downloading'"
            @click="startInAppUpdateDownload"
          >
            {{ updateDownloadState.status === 'downloading' ? `正在下载 (${updateDownloadState.progress}%)...` : '立即下载并安装' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 全局轻量悬浮通知 (成功绿色 / 失败红色，自动消失无需确认) -->
    <TransitionGroup name="gb-toast" tag="div" class="gb-toast-container">
      <div
        v-for="item in appToasts"
        :key="item.id"
        class="gb-toast-item"
        :class="item.type === 'error' ? 'is-error' : 'is-success'"
      >
        <span class="gb-toast-icon">
          <AppIcon :name="item.type === 'error' ? 'alert_triangle' : 'check_circle'" :size="16" />
        </span>
        <span class="gb-toast-text">{{ item.message }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
/* ==========================================================================
   Apple HIG 跨平台五级空间底色与语义色彩系统 (L0 ~ L4 Spatial Surfaces)
   参考规范: CraftSchema / Apple Human Interface Guidelines (Color, Dark Mode, Materials)
   ========================================================================== */
.app-layout {
  /* 字体与精细圆角系统 (Corner Radius) */
  --sys-font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "PingFang SC", "Helvetica Neue", sans-serif;
  --sys-radius-window: 12px;
  --sys-radius-card: 10px;
  --sys-radius-control: 7px;
  --sys-radius-pill: 9999px;
  --sys-radius-badge: 5px;

  /* Apple Unified System Colors (Dark Mode - 2025/2026 HIG Spec) */
  --sys-accent-blue: #0A84FF;
  --sys-accent-blue-hover: #409CFF;
  --sys-tint-blue: rgba(10, 132, 255, 0.16);
  --sys-tint-blue-border: rgba(10, 132, 255, 0.34);

  --sys-accent-pink: #FF375F;
  --sys-accent-pink-hover: #FF6482;
  --sys-tint-pink: rgba(255, 55, 95, 0.16);
  --sys-tint-pink-border: rgba(255, 55, 95, 0.34);

  --sys-accent-green: #30D158;
  --sys-tint-green: rgba(48, 209, 88, 0.16);
  --sys-tint-green-border: rgba(48, 209, 88, 0.32);

  --sys-accent-orange: #FF9F0A;
  --sys-tint-orange: rgba(255, 159, 10, 0.16);
  --sys-tint-orange-border: rgba(255, 159, 10, 0.34);

  --sys-accent-red: #FF453A;
  --sys-tint-red: rgba(255, 69, 58, 0.16);
  --sys-tint-red-border: rgba(255, 69, 58, 0.34);

  --sys-accent-purple: #BF5AF2;
  --sys-tint-purple: rgba(191, 90, 242, 0.16);

  --sys-accent-indigo: #6D7CFF;
  --sys-tint-indigo: rgba(109, 124, 255, 0.16);
  --sys-tint-indigo-border: rgba(109, 124, 255, 0.34);

  --sys-accent-gray: #98989D;
  --color-primary: var(--sys-accent-blue);

  /* 五级空间底色阶梯 (Dark - Midnight Graphite & Elevated Carbon)
     L0 导航轨 (#111113) -> L2 主画布 (#141416) -> L1 次级侧栏 (#18181A) -> L3 阅读器 (#1C1C1E) / 卡片 (#242426) -> L4 浮层 (#2C2C2E) */
  --bg-rail: #111113;
  --bg-app: #141416;
  --bg-canvas: #141416;
  --bg-sidebar: #18181A;
  --bg-navbar: rgba(24, 24, 26, 0.86);
  --bg-subtle: #1C1C1E;
  --bg-reader: #1C1C1E;
  --bg-input: #1C1C1E;
  --bg-card: #242426;
  --bg-card-hover: #2C2C2E;
  --bg-hover: #2C2C2E;
  --bg-elevated: rgba(44, 44, 46, 0.90);

  /* 1px 半透明发丝分割线与微高光玻璃内边 (Separators & Hairlines) */
  --border-color: rgba(255, 255, 255, 0.08);
  --border-strong: rgba(255, 255, 255, 0.15);
  --border-glass: rgba(255, 255, 255, 0.12);
  --border-focus: var(--sys-accent-blue);

  /* 四级语义文本层级 (Label ~ Quaternary Label) */
  --text-main: #F5F5F7;
  --text-secondary: #A1A1A6;
  --text-muted: #6E6E73;
  --text-dim: #48484A;

  /* 按钮与阴影状态 */
  --btn-sec-bg: rgba(255, 255, 255, 0.08);
  --btn-sec-text: #F5F5F7;
  --shadow-color: rgba(0, 0, 0, 0.50);
  --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.35), 0 0 0 0.5px rgba(255, 255, 255, 0.05);
  --shadow-md: 0 10px 28px rgba(0, 0, 0, 0.48), 0 0 0 0.5px rgba(255, 255, 255, 0.08);

  --bg-main: var(--bg-app);
  --bg-secondary: var(--bg-sidebar);

  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  min-width: 0;
  min-height: 0;
  width: 100vw;
  height: 100vh;
  margin: 0;
  padding: 0;
  background-color: var(--bg-app);
  color: var(--text-main);
  font-family: var(--sys-font-family);
  overflow: hidden;
  transition: background-color 0.2s cubic-bezier(0.4, 0, 0.2, 1), color 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.app-layout[data-theme="light"] {
  /* Apple Unified System Colors (Light Mode - 2025/2026 HIG Spec) */
  --sys-accent-blue: #007AFF;
  --sys-accent-blue-hover: #0062CC;
  --sys-tint-blue: rgba(0, 122, 255, 0.10);
  --sys-tint-blue-border: rgba(0, 122, 255, 0.26);

  --sys-accent-pink: #FF2D55;
  --sys-accent-pink-hover: #E01E45;
  --sys-tint-pink: rgba(255, 45, 85, 0.10);
  --sys-tint-pink-border: rgba(255, 45, 85, 0.26);

  --sys-accent-green: #34C759;
  --sys-tint-green: rgba(52, 199, 89, 0.12);
  --sys-tint-green-border: rgba(52, 199, 89, 0.28);

  --sys-accent-orange: #FF9500;
  --sys-tint-orange: rgba(255, 149, 0, 0.12);
  --sys-tint-orange-border: rgba(255, 149, 0, 0.28);

  --sys-accent-red: #FF3B30;
  --sys-tint-red: rgba(255, 59, 48, 0.10);
  --sys-tint-red-border: rgba(255, 59, 48, 0.26);

  --sys-accent-purple: #AF52DE;
  --sys-tint-purple: rgba(175, 82, 222, 0.12);

  --sys-accent-indigo: #6155F5;
  --sys-tint-indigo: rgba(97, 85, 245, 0.10);
  --sys-tint-indigo-border: rgba(97, 85, 245, 0.26);

  --sys-accent-gray: #8E8E93;
  --color-primary: var(--sys-accent-blue);

  /* 五级空间底色阶梯 (Light - Daylight Frosted Glass)
     L0 导航轨 (#EBEBF0) -> L1 次级侧栏 (#F2F2F7, systemGray6) -> L2 主画布 (#F5F5F7, GroupedBg) -> L3 卡片与阅读器 (#FFFFFF, Pure Paper) */
  --bg-rail: #EBEBF0;
  --bg-sidebar: #F2F2F7;
  --bg-navbar: rgba(242, 242, 247, 0.86);
  --bg-app: #F5F5F7;
  --bg-canvas: #F5F5F7;
  --bg-subtle: #EFEFF4;
  --bg-card: #FFFFFF;
  --bg-card-hover: #F8F8FA;
  --bg-hover: #F2F2F7;
  --bg-reader: #FFFFFF;
  --bg-input: #FFFFFF;
  --bg-elevated: rgba(255, 255, 255, 0.90);

  /* 1px 半透明发丝分割线 (iOS/macOS System Separator: rgba(60,60,67,0.10)) */
  --border-color: rgba(60, 60, 67, 0.10);
  --border-strong: rgba(60, 60, 67, 0.18);
  --border-glass: rgba(255, 255, 255, 0.75);
  --border-focus: var(--sys-accent-blue);

  /* 四级语义文本层级 (Label ~ Quaternary Label) */
  --text-main: #1D1D1F;
  --text-secondary: #6E6E73;
  --text-muted: #8E8E93;
  --text-dim: #C7C7CC;

  /* 按钮与阴影状态 */
  --btn-sec-bg: rgba(60, 60, 67, 0.06);
  --btn-sec-text: #1D1D1F;
  --shadow-color: rgba(0, 0, 0, 0.08);
  --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.03);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.09), 0 1px 3px rgba(0, 0, 0, 0.05);

  --bg-main: var(--bg-app);
  --bg-secondary: var(--bg-sidebar);
}

/* ==================== Apple HIG & Google M3 核心组件样式 ==================== */
/* macOS 三色交通灯 */
.traffic-lights {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-right: 6px;
}
.traffic-light {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  display: inline-block;
  opacity: 0.9;
  transition: opacity 0.15s;
}
.traffic-lights:hover .traffic-light {
  opacity: 1;
}
.traffic-light.close {
  background-color: #FF5F56;
  border: 1px solid rgba(224, 68, 62, 0.5);
}
.traffic-light.min {
  background-color: #FFBD2E;
  border: 1px solid rgba(222, 161, 35, 0.5);
}
.traffic-light.max {
  background-color: #27C93F;
  border: 1px solid rgba(26, 171, 41, 0.5);
}

/* macOS 品牌区域 */
.brand-info {
  display: flex;
  flex-direction: column;
}
.brand-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.brand-app-name {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-main);
}
.platform-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: var(--sys-radius-badge);
  background: rgba(0, 122, 255, 0.12);
  color: var(--sys-accent-blue);
}
.platform-badge.bili {
  background: rgba(255, 45, 85, 0.12);
  color: var(--sys-accent-pink);
}

/* Apple Segmented Control 分段胶囊控制器 */
.segmented-control {
  background: var(--btn-sec-bg);
  border-radius: var(--sys-radius-control);
  padding: 2px;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: 1px solid var(--border-color);
}
.segmented-control button {
  background: transparent;
  border: none;
  border-radius: calc(var(--sys-radius-control) - 2px);
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  transition: all 0.18s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
}
.segmented-control button:hover {
  color: var(--text-main);
}
.segmented-control button.active {
  background: var(--bg-card);
  color: var(--text-main);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12), 0 0.5px 1px rgba(0, 0, 0, 0.08);
}
.theme-switch button {
  padding: 4px 7px;
}

/* 平台图标图片 */
.platform-logo-img {
  width: 100%;
  height: 100%;
  border-radius: var(--sys-radius-control);
  object-fit: cover;
  transition: transform 0.15s ease;
}
.platform-group-header:hover .platform-logo-img {
  transform: scale(1.06);
}

/* 导航栏 */
.navbar {
  height: 56px;
  background-color: var(--bg-navbar);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  flex-shrink: 0;
  gap: 20px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.brand-logo-img {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
  pointer-events: none;
  user-select: none;
}
.brand-icon {
  font-size: 26px;
}
.brand-text h1 {
  font-size: 18px;
  font-weight: 700;
  margin: 0;
  color: var(--sys-accent-blue);
  letter-spacing: 0.5px;
}
.brand-text .sub {
  font-size: 11px;
  color: var(--text-muted);
}

/* 导航居中搜索栏 */
.navbar-search {
  position: relative;
  display: flex;
  align-items: center;
  width: 380px;
  max-width: 45%;
}
.search-icon {
  position: absolute;
  left: 12px;
  font-size: 13px;
  pointer-events: none;
  opacity: 0.6;
}
.nav-search-input {
  width: 100%;
  padding: 7px 32px 7px 34px;
  background: var(--bg-input);
  color: var(--text-main);
  border: 1px solid var(--border-color);
  border-radius: 20px;
  font-size: 13px;
  outline: none;
  transition: all 0.2s;
}
.nav-search-input:focus {
  border-color: var(--border-focus);
  box-shadow: 0 0 0 2px var(--sys-tint-blue);
}
.btn-clear-search {
  position: absolute;
  right: 12px;
  background: transparent;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 12px;
}

.navbar-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.btn-tool {
  background: var(--bg-card);
  color: var(--text-main);
  border: 1px solid var(--border-color);
  font-size: 13px;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s;
}
.btn-tool:hover {
  background: var(--bg-card-hover);
  border-color: var(--border-focus);
}
.badge-pulse {
  background: var(--sys-accent-blue);
  color: white;
  font-size: 11px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 10px;
  animation: pulse 1.5s infinite;
}
.badge-count {
  background: var(--border-color);
  color: var(--text-muted);
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 10px;
}
@keyframes pulse {
  0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(2, 132, 199, 0.7); }
  70% { transform: scale(1.05); box-shadow: 0 0 0 6px rgba(2, 132, 199, 0); }
  100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(2, 132, 199, 0); }
}
.server-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  font-family: inherit;
  border: 1px solid transparent;
  cursor: pointer;
  white-space: nowrap;
  color: var(--sys-accent-green);
  background: var(--sys-tint-green);
  padding: 4px 10px;
  border-radius: 20px;
}
.server-status.offline {
  color: var(--sys-accent-red);
  background: var(--sys-tint-red);
}
.server-status .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: currentColor;
}

/* 主体容器 */
.main-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* 侧边栏 */
.sidebar {
  width: 256px;
  background-color: var(--bg-sidebar);
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.sidebar-mode-switcher {
  display: flex;
  align-items: center;
  padding: 8px 8px;
  background: var(--bg-subtle);
  border-bottom: 1px solid var(--border-color);
  gap: 5px;
  white-space: nowrap !important;
  flex-shrink: 0;
}
.mode-btn {
  flex: 1;
  min-width: 0;
  background: var(--bg-card);
  color: var(--text-muted);
  border: 1px solid var(--border-color);
  height: 30px;
  padding: 0 4px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  white-space: nowrap !important;
  flex-shrink: 0;
  line-height: 1;
}
.mode-btn span {
  white-space: nowrap !important;
  word-break: keep-all;
  overflow: hidden;
  text-overflow: clip;
}
.mode-btn:hover {
  color: var(--text-main);
  background: var(--bg-card-hover);
}
.mode-btn.active {
  background: var(--sys-tint-pink);
  color: var(--sys-accent-pink);
  border-color: var(--sys-tint-pink-border);
  font-weight: 600;
}
.sidebar-empty-tip {
  padding: 16px 12px;
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
}
.sidebar-section {
  padding: 16px;
  border-bottom: 1px solid var(--border-color);
}
.section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--text-secondary);
  margin-bottom: 10px;
}
.btn-icon {
  background: transparent;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 14px;
}
.nav-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.nav-item {
  white-space: nowrap !important;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 6px;
  background: transparent;
  border: none;
  color: var(--text-main);
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
  font-size: 13px;
}
.nav-item:hover {
  background-color: var(--bg-card-hover);
  color: var(--text-main);
}
.nav-item.active {
  background-color: var(--sys-tint-pink);
  color: var(--sys-accent-pink);
  font-weight: 600;
}
.nav-tag-badge {
  font-size: 10px;
  background: var(--sys-tint-pink);
  color: var(--sys-accent-pink);
  padding: 2px 6px;
  border-radius: 10px;
  margin-left: auto;
}
.nav-count {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-secondary);
}

.up-list-section {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.up-scroll-box {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.btn-clear-filter {
  background: transparent;
  border: 1px solid var(--border-strong);
  color: var(--sys-accent-blue);
  font-size: 11px;
  border-radius: 4px;
  padding: 1px 6px;
  cursor: pointer;
}
.btn-clear-filter:hover {
  background: var(--sys-tint-blue);
}
.up-mini-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.up-mini-card:hover {
  background-color: var(--bg-card-hover);
}
.up-mini-card.active {
  background-color: var(--sys-tint-pink);
  color: var(--sys-accent-pink);
}
.up-mini-card.active .up-name {
  color: var(--sys-accent-pink);
  font-weight: 600;
}
.up-avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  object-fit: cover;
}
.up-name {
  font-size: 12px;
  color: var(--text-main);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 二级侧边栏 UP主 实时搜索框 */
.up-search-bar {
  position: relative;
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}
.up-search-icon {
  position: absolute;
  left: 8px;
  color: var(--text-muted);
  pointer-events: none;
}
.up-search-input {
  width: 100%;
  height: 28px;
  padding: 0 24px 0 26px;
  border-radius: 6px;
  border: 1px solid var(--border-color);
  background: var(--bg-input);
  color: var(--text-main);
  font-size: 11px;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.up-search-input:focus {
  border-color: var(--sys-accent-blue);
  box-shadow: var(--focus-ring);
}
.up-search-clear {
  position: absolute;
  right: 5px;
  width: 16px;
  height: 16px;
  border: none;
  border-radius: 50%;
  background: var(--bg-hover);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 0;
}
.up-search-clear:hover {
  background: var(--border-strong);
  color: var(--text-main);
}

/* 右侧主视窗 */
.content-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background-color: var(--bg-app);
}

/* 操作栏 */
.toolbar {
  padding: 12px 20px;
  background-color: var(--bg-navbar);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.toolbar-left, .toolbar-right {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  row-gap: 8px;
  column-gap: 10px;
}
.toolbar-left .btn,
.toolbar-left .btn-batch,
.toolbar-left .btn-folder,
.toolbar-left .rs-pill,
.toolbar-left .stats-badge,
.toolbar-right .switch-control,
.toolbar-right .quality-select {
  white-space: nowrap !important;
  flex-shrink: 0 !important;
}
.stats-badge {
  font-size: 13px;
  color: var(--text-secondary);
}
.stats-badge strong {
  color: var(--sys-accent-blue);
}

.btn-batch {
  background: var(--sys-accent-blue);
  color: white;
  border: none;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.btn-batch:hover:not(:disabled) {
  opacity: 0.9;
  transform: translateY(-1px);
}
.btn-batch:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.btn-folder {
  height: 32px;
  background-color: var(--btn-sec-bg);
  color: var(--btn-sec-text);
  border: 1px solid var(--border-color);
  font-size: 12px;
  padding: 0 12px;
  border-radius: 6px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  transition: background-color 0.15s ease;
}
.btn-folder:hover {
  background-color: var(--bg-card-hover);
  color: var(--text-main);
}

.downloaded-group {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  width: 100%;
  min-width: 0;
}
.card-btn-pair {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
}
.btn-micro {
  height: 26px;
  padding: 0 9px;
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
  flex-shrink: 0;
  gap: 4px;
  background-color: var(--btn-sec-bg);
  color: var(--btn-sec-text);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}
.btn-micro:hover {
  background-color: var(--sys-accent-blue);
  color: #fff;
  border-color: var(--sys-accent-blue);
}

.search-box input {
  height: 32px;
  background-color: var(--bg-input);
  border: 1px solid var(--border-color);
  color: var(--text-main);
  padding: 0 12px;
  border-radius: 6px;
  font-size: 12px;
  width: 220px;
  outline: none;
}
.search-box input:focus {
  border-color: var(--sys-accent-blue);
}

/* 仅音频开关 */
.switch-control {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 13px;
}
.switch-control input {
  display: none;
}
.switch-slider {
  width: 36px;
  height: 20px;
  background-color: var(--border-strong);
  border-radius: 20px;
  position: relative;
  transition: 0.2s;
}
.switch-slider:before {
  content: "";
  position: absolute;
  width: 14px;
  height: 14px;
  left: 3px;
  bottom: 3px;
  background-color: white;
  border-radius: 50%;
  transition: 0.2s;
}
.switch-control input:checked + .switch-slider {
  background-color: var(--sys-accent-pink);
}
.switch-control input:checked + .switch-slider:before {
  transform: translateX(16px);
}
.kb-badge {
  font-size: 10px;
  background: var(--sys-tint-pink);
  color: var(--sys-accent-pink);
  padding: 2px 6px;
  border-radius: 4px;
}
.quality-select {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-secondary);
}
.quality-select select {
  height: 32px;
  background-color: var(--bg-input);
  border: 1px solid var(--border-color);
  color: var(--text-main);
  padding: 0 8px;
  border-radius: 6px;
  font-size: 12px;
}

/* 视频网格 */
.video-container {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
.video-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 20px;
}
.video-card {
  background-color: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.2s, border-color 0.2s;
}
.video-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
  border-color: var(--border-strong);
}
.cover-wrapper {
  position: relative;
  aspect-ratio: 16 / 9;
  cursor: pointer;
  background-color: var(--bg-subtle);
}
.cover-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.duration-tag {
  position: absolute;
  right: 6px;
  bottom: 6px;
  background: rgba(0, 0, 0, 0.75);
  color: #ffffff;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
}
.play-tag {
  position: absolute;
  left: 6px;
  bottom: 6px;
  background: rgba(0, 0, 0, 0.75);
  color: #ffffff;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
}
.card-details {
  padding: 12px;
  display: flex;
  flex-direction: column;
  flex: 1;
}
.video-title {
  font-size: 14px;
  line-height: 1.4;
  font-weight: 600;
  margin: 0 0 8px 0;
  color: var(--text-main);
  cursor: pointer;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.video-title:hover {
  color: var(--sys-accent-blue);
}
.card-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 12px;
}
.author-name {
  color: var(--text-secondary);
  font-weight: 500;
}
.card-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-top: auto;
  border-top: 1px solid var(--border-color);
  padding-top: 10px;
  min-height: 36px;
}
.bvid-tag {
  font-size: 11px;
  color: var(--text-muted);
  font-family: monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.restricted-cover-tag {
  position: absolute;
  top: 8px;
  right: 8px;
  background: rgba(245, 158, 11, 0.92);
  color: #ffffff;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(4px);
}
.is-restricted-card .cover-img {
  filter: saturate(0.75);
}

/* 按钮样式 */
.btn {
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  border: none;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-primary {
  background-color: var(--sys-accent-blue);
  color: white;
}
.btn-primary:hover {
  background-color: var(--sys-accent-blue-hover);
}
.btn-secondary {
  background-color: var(--btn-sec-bg);
  color: var(--btn-sec-text);
}
.btn-secondary:hover {
  background-color: var(--bg-card-hover);
}
.btn-outline {
  background: transparent;
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
}
.btn-outline:hover {
  background-color: var(--bg-card-hover);
  color: var(--text-main);
}
.btn-download {
  background-color: var(--sys-accent-blue);
  color: white;
  font-size: 12px;
  height: 26px;
  padding: 0 10px;
  line-height: 1;
  white-space: nowrap;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.btn-download:hover {
  background-color: var(--sys-accent-blue-hover);
}

.status-badge {
  font-size: 11px;
  height: 26px;
  padding: 0 8px;
  line-height: 1;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  flex-shrink: 0;
  font-weight: 500;
}
.status-badge.downloading {
  background: var(--sys-tint-blue);
  color: var(--sys-accent-blue);
  max-width: 145px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.status-badge.pending {
  background: var(--sys-tint-orange);
  color: var(--sys-accent-orange);
}
.status-badge.success {
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
}
.status-badge.failed {
  background: var(--sys-tint-red);
  color: var(--sys-accent-red);
}
.status-badge.restricted {
  background: var(--sys-tint-orange);
  color: var(--sys-accent-orange);
  border: 1px solid var(--sys-tint-orange-border);
  cursor: not-allowed;
}
.retry-btn {
  background: var(--sys-accent-red) !important;
  color: white !important;
}
.retry-btn:hover {
  background: var(--sys-accent-red) !important;
}

/* 弹窗样式 */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background-color: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.modal-card {
  width: 440px;
  background-color: var(--bg-elevated);
  backdrop-filter: blur(25px) saturate(180%);
  -webkit-backdrop-filter: blur(25px) saturate(180%);
  border: 1px solid var(--border-strong);
  border-radius: 12px;
  box-shadow: var(--shadow-md);
  overflow: hidden;
}
.modal-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.modal-header h3 {
  margin: 0;
  font-size: 16px;
  color: var(--text-main);
}
.btn-close {
  background: transparent;
  border: none;
  color: var(--text-secondary);
  font-size: 16px;
  cursor: pointer;
}
.modal-body {
  padding: 20px;
}
.qr-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
.qr-wrapper {
  width: 200px;
  height: 200px;
  background: white;
  border-radius: 8px;
  padding: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 14px;
}
.qr-img {
  width: 100%;
  height: 100%;
}
.qr-status {
  font-size: 14px;
  font-weight: 600;
  color: var(--sys-accent-blue);
  margin-bottom: 6px;
}
.qr-tip {
  font-size: 11px;
  color: var(--text-secondary);
}
.cookie-textarea {
  width: 100%;
  box-sizing: border-box;
  background: var(--bg-input);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  color: var(--text-main);
  padding: 10px;
  font-family: monospace;
  font-size: 12px;
  resize: vertical;
}
.input-tip {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}
.error-text {
  color: var(--sys-accent-red);
  font-size: 12px;
  margin-top: 8px;
}
.modal-footer {
  padding: 12px 20px;
  background-color: var(--bg-subtle);
  border-top: 1px solid var(--border-color);
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

/* 动画 */
.spinning {
  display: inline-block;
  animation: spin 1s linear infinite;
}
@keyframes spin {
  100% {
    transform: rotate(360deg);
  }
}
.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--sys-tint-blue);
  border-top-color: var(--sys-accent-blue);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
.loading-state, .empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  color: var(--text-secondary);
  gap: 12px;
}

/* 全局任务抽屉 Drawer */
.drawer-backdrop {
  position: fixed;
  inset: 0;
  background-color: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(3px);
  z-index: 1000;
  display: flex;
  justify-content: flex-end;
  animation: fadeIn 0.2s ease-out;
}
.drawer-panel {
  width: 500px;
  max-width: 92vw;
  height: 100%;
  background-color: var(--bg-elevated);
  backdrop-filter: blur(25px) saturate(180%);
  -webkit-backdrop-filter: blur(25px) saturate(180%);
  border-left: 1px solid var(--border-strong);
  box-shadow: var(--shadow-md);
  display: flex;
  flex-direction: column;
  animation: slideInRight 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes slideInRight {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
.drawer-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg-card);
}
.drawer-title-group h3 {
  margin: 0;
  font-size: 16px;
  color: var(--text-main);
}
.task-summary {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 3px;
  display: block;
}
.drawer-actions-bar {
  padding: 10px 16px;
  background: var(--bg-subtle);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.btn-drawer-tool {
  background: var(--btn-sec-bg);
  color: var(--btn-sec-text);
  border: 1px solid var(--border-color);
  font-size: 12px;
  height: 28px;
  padding: 0 10px;
  border-radius: 6px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  transition: all 0.2s;
}
.btn-drawer-tool:hover:not(:disabled) {
  background: var(--bg-card-hover);
  color: var(--text-main);
  border-color: var(--border-strong);
}
.btn-drawer-tool:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.btn-drawer-tool.danger {
  color: var(--sys-accent-red);
  border-color: var(--sys-tint-red-border);
}
.btn-drawer-tool.danger:hover {
  background: var(--sys-tint-red);
}
.reconcile-alert {
  background: var(--sys-tint-blue);
  border-left: 3px solid var(--sys-accent-blue);
  color: var(--sys-accent-blue);
  font-size: 12px;
  padding: 8px 16px;
}
.drawer-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-subtle);
}
.drawer-tabs button {
  flex: 1;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-secondary);
  padding: 10px 4px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}
.drawer-tabs button:hover {
  color: var(--text-main);
}
.drawer-tabs button.active {
  color: var(--sys-accent-blue);
  border-bottom-color: var(--sys-accent-blue);
  font-weight: 600;
  background: var(--sys-tint-blue);
}
.drawer-task-list {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.drawer-empty {
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
  padding: 60px 0;
}
.task-item-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: border-color 0.2s;
}
.task-item-card:hover {
  border-color: var(--border-strong);
}
.task-card-header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.task-type-badge {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--sys-accent-blue-hover);
  color: white;
  white-space: nowrap;
}
.task-type-badge.audio {
  background: var(--sys-accent-purple);
}
.task-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.task-status-badge {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  white-space: nowrap;
}
.task-status-badge.downloading {
  background: var(--sys-tint-blue);
  color: var(--sys-accent-blue);
}
.task-status-badge.pending {
  background: var(--sys-tint-orange);
  color: var(--sys-accent-orange);
}
.task-status-badge.success {
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
}
.task-status-badge.failed {
  background: var(--sys-tint-red);
  color: var(--sys-accent-red);
}
.task-card-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-secondary);
}
.task-bvid {
  font-family: monospace;
  color: var(--text-muted);
}
.task-progress-box {
  margin-top: 4px;
}
.task-progress-bar {
  height: 6px;
  background: var(--border-color);
  border-radius: 3px;
  overflow: hidden;
}
.task-progress-fill {
  height: 100%;
  background: var(--sys-accent-blue);
  transition: width 0.3s ease;
}
.task-progress-info {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 4px;
}
.task-speed {
  color: var(--sys-accent-blue);
  font-family: monospace;
}
.task-error-box {
  font-size: 11px;
  color: var(--sys-accent-red);
  background: var(--sys-tint-red);
  padding: 6px 8px;
  border-radius: 4px;
  word-break: break-all;
}
.task-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--border-color);
  padding-top: 8px;
}
.task-time {
  font-size: 11px;
  color: var(--text-muted);
}
.task-btn-group {
  display: flex;
  gap: 6px;
}
.settings-modal {
  width: 520px;
}
.setting-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.setting-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
}
.setting-input {
  width: 100%;
  box-sizing: border-box;
  background: var(--bg-input);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  color: var(--text-main);
  padding: 8px 12px;
  font-size: 13px;
}
.setting-input.readonly {
  background: var(--bg-subtle);
  color: var(--text-muted);
  cursor: not-allowed;
}
.setting-tip {
  font-size: 11px;
  color: var(--text-secondary);
  margin: 0;
  line-height: 1.5;
}
.setting-tip code {
  color: var(--sys-accent-blue);
  font-family: monospace;
  background: var(--sys-tint-blue);
  padding: 1px 4px;
  border-radius: 3px;
}

/* 接口限流与令牌桶调度中心样式 */
.btn-limiter {
  position: relative;
}
.limiter-status-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: var(--sys-accent-green);
  display: inline-block;
}
.limiter-status-indicator.cooling {
  background-color: var(--sys-accent-red);
  animation: pulse-red 1s infinite;
}
.limiter-status-indicator.paused {
  background-color: var(--sys-accent-orange);
}
@keyframes pulse-red {
  0% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
  70% { transform: scale(1.1); box-shadow: 0 0 0 6px rgba(239, 68, 68, 0); }
  100% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
}

.limiter-modal {
  width: 780px;
  max-width: 95vw;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
}
.limiter-header-title {
  display: flex;
  align-items: center;
  gap: 12px;
}
.limiter-header-title h3 {
  margin: 0;
  font-size: 16px;
  color: var(--text-main);
}
.limiter-status-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 12px;
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.limiter-status-tag.cooling {
  background: var(--sys-tint-red);
  color: var(--sys-accent-red);
  font-weight: 600;
}
.limiter-status-tag.paused {
  background: var(--sys-tint-orange);
  color: var(--sys-accent-orange);
}
.limiter-status-tag .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: currentColor;
}
.limiter-body {
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 仪表盘 */
.limiter-dashboard-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.metric-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.metric-label {
  font-size: 11px;
  color: var(--text-secondary);
  font-weight: 500;
}
.metric-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-main);
  line-height: 1.1;
  font-family: monospace;
}
.metric-sub {
  font-size: 13px;
  color: var(--text-muted);
  font-weight: normal;
}
.token-water-bar {
  height: 6px;
  background: var(--border-color);
  border-radius: 3px;
  overflow: hidden;
  margin-top: 4px;
}
.token-water-fill {
  height: 100%;
  background: var(--sys-accent-blue);
  transition: width 0.3s ease;
}
.metric-hint {
  font-size: 11px;
  color: var(--text-muted);
}
.warning-text {
  color: var(--sys-accent-orange) !important;
}
.success-text {
  color: var(--sys-accent-green);
}
.danger-text {
  color: var(--sys-accent-red);
}

/* 告警条 */
.ban-alert-box {
  background: var(--sys-tint-red);
  border: 1px solid var(--sys-tint-red-border);
  border-radius: 8px;
  padding: 10px 14px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  color: var(--sys-accent-red);
  font-size: 12px;
  line-height: 1.4;
}
.ban-alert-box .alert-icon {
  font-size: 18px;
}
.ban-alert-box p {
  margin: 3px 0 0 0;
  color: var(--text-secondary);
}
.ban-alert-box strong {
  color: var(--sys-accent-red);
}

/* 快捷控制 */
.limiter-quick-actions {
  display: flex;
  gap: 10px;
}
.btn-warning {
  background-color: var(--sys-accent-orange);
  color: white;
}
.btn-warning:hover {
  background-color: var(--sys-accent-orange);
}

/* 分割线 */
.section-divider {
  display: flex;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-top: 4px;
}
.section-divider::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--border-color);
  margin-left: 10px;
}

/* 预设卡片 */
.preset-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}
.preset-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.preset-card:hover {
  border-color: var(--border-strong);
  background: var(--bg-card-hover);
}
.preset-card.active {
  border-color: var(--sys-accent-blue);
  background: var(--sys-tint-blue);
  box-shadow: 0 0 0 1px var(--sys-accent-blue);
}
.preset-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-main);
}
.preset-desc {
  font-size: 11px;
  color: var(--text-secondary);
  font-family: monospace;
}
.preset-tag {
  font-size: 10px;
  color: var(--sys-accent-blue);
  margin-top: 2px;
}

/* 滑块控件 */
.limiter-controls-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px 20px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 14px 16px;
}
.control-row {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.control-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.control-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-main);
}
.control-val {
  font-size: 12px;
  font-weight: 700;
  color: var(--sys-accent-blue);
  font-family: monospace;
}
.slider {
  width: 100%;
  accent-color: var(--sys-accent-blue);
  cursor: pointer;
}
.control-desc {
  font-size: 10px;
  color: var(--text-muted);
  line-height: 1.2;
}

/* 日志流表格 */
.log-stream-table-wrapper {
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-card);
}
.log-stream-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  font-family: monospace;
  text-align: left;
}
.log-stream-table th {
  background: var(--bg-subtle);
  color: var(--text-muted);
  padding: 6px 10px;
  font-weight: 600;
  position: sticky;
  top: 0;
  z-index: 1;
}
.log-stream-table td {
  padding: 5px 10px;
  border-bottom: 1px solid var(--border-color);
  color: var(--text-secondary);
}
.log-stream-table tr:hover {
  background: rgba(255, 255, 255, 0.03);
}
.row-banned {
  background: var(--sys-tint-red) !important;
}
.method-tag {
  color: var(--sys-accent-blue);
  font-weight: 700;
}
.log-url {
  max-width: 220px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.status-code-tag {
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 10px;
}
.status-code-tag.ok {
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
}
.status-code-tag.err {
  background: var(--sys-tint-red);
  color: var(--sys-accent-red);
}
.banned-tag {
  background: var(--sys-accent-red);
  color: white;
  padding: 1px 5px;
  border-radius: 3px;
  font-weight: 700;
  font-size: 10px;
}
.code-ok {
  color: var(--sys-accent-green);
}
.code-warn {
  color: var(--sys-accent-orange);
}
.log-duration {
  color: var(--text-secondary);
}
.text-center {
  text-align: center;
}
.text-muted {
  color: var(--text-muted);
}

/* ======================================================================
   底部账号与设置栏 (Sidebar Footer)
====================================================================== */
.sidebar-footer {
  position: relative;
  padding: 10px 12px;
  background: var(--bg-sidebar);
  border-top: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: auto;
}
.sidebar-user-card {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  overflow: hidden;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 8px;
  transition: background 0.15s;
}
.sidebar-user-card:hover {
  background: var(--bg-card-hover);
}
.user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  object-fit: cover;
  border: 1.5px solid var(--sys-accent-blue);
  flex-shrink: 0;
}
.user-avatar-placeholder {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  flex-shrink: 0;
}
.user-details {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  line-height: 1.25;
}
.user-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-main);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.user-sub {
  font-size: 10px;
  color: var(--text-muted);
}
.vip-tag {
  background: var(--sys-accent-pink);
  color: white;
  font-size: 9px;
  padding: 1px 4px;
  border-radius: 3px;
  font-weight: 600;
}
.user-pop-arrow {
  font-size: 9px;
  color: var(--text-dim);
  margin-left: auto;
}
.sidebar-footer-tools {
  display: flex;
  gap: 4px;
}
.footer-tool-btn {
  background: transparent;
  border: 1px solid var(--border-color);
  color: var(--text-muted);
  font-size: 13px;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
}
.footer-tool-btn:hover {
  background: var(--bg-card-hover);
  color: var(--text-main);
}

/* 多账号管理悬浮层 */
.account-popover {
  position: absolute;
  bottom: 56px;
  left: 8px;
  width: 240px;
  background: var(--bg-elevated);
  backdrop-filter: blur(25px) saturate(180%);
  -webkit-backdrop-filter: blur(25px) saturate(180%);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  box-shadow: 0 10px 25px var(--shadow-color);
  padding: 10px;
  z-index: 100;
  animation: popSlideUp 0.15s ease-out;
}
@keyframes popSlideUp {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
.popover-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  font-weight: 700;
  color: var(--text-muted);
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 8px;
}
.account-list {
  max-height: 180px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.account-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
  border: 1px solid transparent;
}
.account-row:hover {
  background: var(--bg-card-hover);
}
.account-row.active {
  background: var(--sys-tint-blue);
  border-color: var(--sys-tint-blue-border);
}
.acc-face {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  object-fit: cover;
}
.acc-info {
  flex: 1;
  overflow: hidden;
  line-height: 1.2;
}
.acc-name-line {
  display: flex;
  align-items: center;
  gap: 4px;
}
.acc-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-main);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tag-current {
  font-size: 9px;
  background: var(--sys-accent-blue);
  color: white;
  padding: 0 4px;
  border-radius: 3px;
}
.acc-uid {
  font-size: 10px;
  color: var(--text-muted);
}
.btn-acc-del {
  background: transparent;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  font-size: 12px;
  padding: 2px 4px;
  border-radius: 4px;
}
.btn-acc-del:hover {
  color: var(--sys-accent-red);
  background: var(--sys-tint-red);
}
.popover-actions {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border-color);
}
.btn-pop-action {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  color: var(--text-main);
  font-size: 11px;
  padding: 5px 8px;
  border-radius: 5px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
}
.btn-pop-action:hover {
  background: var(--bg-card-hover);
  border-color: var(--border-focus);
}
.btn-pop-action.text-danger {
  color: var(--sys-accent-red);
}

/* ======================================================================
   统一设置中心模态框 (Settings Center Modal)
====================================================================== */
.settings-center-modal {
  width: 820px;
  max-width: 95vw;
  height: 680px;
  max-height: 92vh;
  display: flex;
  flex-direction: column;
}
.settings-header-title h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--text-main);
}
.settings-subtitle {
  font-size: 11px;
  color: var(--text-muted);
}
.settings-tab-nav {
  display: flex;
  gap: 4px;
  padding: 0 20px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-color);
}
.tab-nav-btn {
  padding: 10px 16px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}
.tab-nav-btn:hover {
  color: var(--text-main);
}
.tab-nav-btn.active {
  color: var(--sys-accent-blue);
  border-bottom-color: var(--sys-accent-blue);
}
.settings-center-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
.tab-pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.setting-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 16px;
}
.setting-card.disabled-card {
  opacity: 0.55;
  pointer-events: none;
}
.setting-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.setting-title {
  margin: 0 0 4px 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-main);
}
.setting-desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.4;
}
.worker-val-tag {
  background: var(--sys-accent-blue);
  color: white;
  font-size: 12px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 12px;
  white-space: nowrap;
}
.worker-slider-row {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 14px;
}
.worker-slider-row .slider {
  flex: 1;
}
.worker-num-input {
  width: 60px;
  padding: 4px 8px;
  background: var(--bg-input);
  color: var(--text-main);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  font-size: 13px;
  text-align: center;
}
.setting-input-row {
  display: flex;
  gap: 10px;
}
.reconcile-msg {
  margin: 10px 0 0 0;
  font-size: 12px;
  color: var(--sys-accent-green);
}
.toggle-switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
  flex-shrink: 0;
}
.toggle-switch input {
  opacity: 0;
  width: 0;
  height: 0;
}
.toggle-slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: var(--border-color);
  transition: 0.3s;
  border-radius: 24px;
}
.toggle-slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 3px;
  bottom: 3px;
  background-color: white;
  transition: 0.3s;
  border-radius: 50%;
}
.toggle-switch input:checked + .toggle-slider {
  background-color: var(--sys-accent-blue);
}
.toggle-switch input:checked + .toggle-slider:before {
  transform: translateX(20px);
}
.setting-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.setting-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
}
.setting-select {
  padding: 6px 10px;
  background: var(--bg-input);
  color: var(--text-main);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  font-size: 13px;
  outline: none;
}
.setting-checkbox-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-main);
  cursor: pointer;
}
.accounts-actions-top {
  display: flex;
  gap: 8px;
}
.setting-accounts-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.acc-card-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
}
.acc-card-item.current {
  border-color: var(--sys-accent-blue);
  background: var(--sys-tint-blue);
}
.acc-card-face {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  object-fit: cover;
}
.acc-card-meta {
  flex: 1;
}
.acc-card-title-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.acc-card-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-main);
}
.tag-current-badge {
  background: var(--sys-accent-blue);
  color: white;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 10px;
}
.acc-card-sub {
  font-size: 11px;
  color: var(--text-muted);
}
.acc-card-actions {
  display: flex;
  gap: 8px;
}
.btn-sm {
  padding: 4px 10px;
  font-size: 12px;
}
.btn-danger-outline {
  background: transparent;
  border: 1px solid var(--sys-accent-red);
  color: var(--sys-accent-red);
  border-radius: 6px;
  cursor: pointer;
}
.btn-danger-outline:hover {
  background: var(--sys-accent-red);
  color: white;
}
.theme-picker-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.theme-card {
  border: 2px solid var(--border-color);
  border-radius: 10px;
  padding: 12px;
  cursor: pointer;
  transition: all 0.2s;
  background: var(--bg-card);
}
.theme-card:hover {
  border-color: var(--sys-accent-blue);
}
.theme-card.active {
  border-color: var(--sys-accent-blue);
  box-shadow: 0 0 0 2px var(--sys-tint-blue);
}
.theme-preview {
  height: 80px;
  border-radius: 6px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(0, 0, 0, 0.1);
}
.dark-preview .prev-nav { height: 16px; background: #18181A; border-bottom: 1px solid rgba(255,255,255,0.08); }
.dark-preview .prev-body { flex: 1; display: flex; }
.dark-preview .prev-side { width: 30%; background: #111113; border-right: 1px solid rgba(255,255,255,0.08); }
.dark-preview .prev-main { flex: 1; background: #141416; }

.light-preview .prev-nav { height: 16px; background: #F2F2F7; border-bottom: 1px solid rgba(60,60,67,0.12); }
.light-preview .prev-body { flex: 1; display: flex; }
.light-preview .prev-side { width: 30%; background: #EBEBF0; border-right: 1px solid rgba(60,60,67,0.12); }
.light-preview .prev-main { flex: 1; background: #F5F5F7; }
.theme-card-info {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
}
.theme-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-main);
}
.theme-tag {
  font-size: 11px;
  color: var(--text-muted);
}

/* ==================== 四栏双级可折叠导航与知乎工作台样式 ==================== */
.primary-platform-rail {
  width: 210px;
  background-color: var(--bg-rail);
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  flex-shrink: 0;
  transition: width 0.2s ease;
  overflow: hidden;
}
.primary-platform-rail.collapsed {
  width: 62px;
}
.rail-scroll {
  padding: 10px 8px;
  overflow-y: auto;
  overflow-x: hidden;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rail-section-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  padding: 2px 6px;
  letter-spacing: 0.5px;
}
.platform-group-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  flex-shrink: 0;
}
.platform-group-card.active {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}
.platform-group-header {
  width: 100%;
  padding: 9px 10px;
  background: transparent;
  border: none;
  color: var(--text-main);
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  font-weight: 600;
  font-size: 13px;
}
.platform-title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.platform-logo {
  width: 24px;
  height: 24px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  flex-shrink: 0;
}
.platform-logo.zhihu { background: var(--sys-accent-blue); }
.platform-logo.bili { background: var(--sys-accent-pink); }
.platform-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.platform-meta {
  display: flex;
  align-items: center;
  gap: 5px;
}
.platform-unread-pill {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  font-weight: 600;
}
.platform-unread-pill.zhihu { background: var(--sys-tint-blue); color: var(--sys-accent-blue); }
.platform-unread-pill.bili { background: var(--sys-tint-pink); color: var(--sys-accent-pink); }
.platform-arrow {
  font-size: 10px;
  color: var(--text-muted);
}
.platform-sub-list {
  border-top: 1px solid var(--border-color);
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: var(--bg-subtle);
}
.platform-sub-btn {
  width: 100%;
  padding: 7px 8px;
  border-radius: 7px;
  border: none;
  background: transparent;
  color: var(--text-muted);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
}
.platform-sub-btn:hover {
  background: var(--bg-card-hover);
  color: var(--text-main);
}
.platform-sub-btn.active {
  background: var(--sys-tint-blue);
  color: var(--sys-accent-blue);
  font-weight: 600;
}
.platform-sub-btn.bili-mode.active {
  background: var(--sys-tint-pink);
  color: var(--sys-accent-pink);
  font-weight: 600;
}
.sub-text {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sub-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  font-weight: 600;
}
.sub-badge.zhihu { background: var(--sys-tint-blue); color: var(--sys-accent-blue); }
.sub-badge.emerald { background: var(--sys-tint-green); color: var(--sys-accent-green); }
.sub-badge.bili { background: var(--sys-tint-pink); color: var(--sys-accent-pink); }

.rail-footer {
  padding: 8px;
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
}
.rail-account-box {
  width: 100%;
  padding: 8px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
  color: var(--text-main);
  font-family: inherit;
  text-align: left;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  transition: border-color 0.15s, background-color 0.15s;
}
.rail-account-box:hover {
  background: var(--bg-card-hover);
  border-color: var(--border-strong);
}
.rail-account-box:focus-visible,
.platform-group-header:focus-visible,
.platform-sub-btn:focus-visible,
.btn-tool:focus-visible,
.btn:focus-visible,
.btn-micro:focus-visible,
.win-ctrl-btn:focus-visible,
.zhihu-source-item:focus-visible,
.up-mini-card:focus-visible {
  outline: 2px solid var(--sys-accent-blue);
  outline-offset: 2px;
}

/* Windows 风格右上角窗口控件 */
.win-window-controls {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: 4px;
  padding-left: 8px;
  border-left: 1px solid var(--border-color);
}
.win-ctrl-btn {
  width: 30px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
}
.win-ctrl-btn:hover {
  background: var(--bg-hover);
  color: var(--text-main);
}
.win-ctrl-btn.close:hover {
  background: #e81123;
  color: #ffffff;
}

/* 首次使用引导卡片与错误空态样式 */
.onboarding-guide-card {
  max-width: 680px;
  margin: 28px auto;
  padding: 24px;
  border-radius: 14px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  box-shadow: var(--shadow-sm);
}
.onboarding-header h3 {
  margin: 0 0 6px;
  font-size: 17px;
  font-weight: 700;
  color: var(--text-main);
}
.onboarding-header p {
  margin: 0 0 18px;
  font-size: 13px;
  color: var(--text-secondary);
}
.onboarding-steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.onboarding-step {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-color);
}
.onboarding-step.current {
  border-color: var(--sys-accent-blue);
  background: var(--sys-tint-blue);
}
.onboarding-step.done .step-num {
  background: var(--sys-accent-green);
  color: #fff;
}
.step-num {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--btn-sec-bg);
  color: var(--text-main);
  font-size: 12px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.step-body {
  flex: 1;
  min-width: 0;
}
.step-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
}
.step-desc {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}
.error-state-box {
  border: 1px solid var(--sys-tint-red-border);
  background: var(--bg-card);
  border-radius: 12px;
  padding: 32px 24px;
  max-width: 520px;
  margin: 32px auto;
}
.state-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-main);
  margin-bottom: 6px;
}
.state-desc {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 14px;
  line-height: 1.5;
}

/* 设置保存规则胶囊与 B站卡片多选框 */
.save-rule-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--btn-sec-bg);
  color: var(--text-secondary);
}
.save-rule-pill.auto {
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
}
.save-rule-pill.manual.dirty {
  background: var(--sys-tint-orange);
  color: var(--sys-accent-orange);
}
.bili-card-checkbox {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 4;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: rgba(17, 17, 19, 0.72);
  backdrop-filter: blur(4px);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.bili-card-checkbox input {
  width: 15px;
  height: 15px;
  cursor: pointer;
  margin: 0;
}
.video-card.is-selected-card {
  border-color: var(--sys-accent-pink);
  box-shadow: 0 0 0 2px var(--sys-tint-pink);
}

@media (max-width: 980px) {
  .nav-btn-label {
    display: none;
  }
  .navbar-search {
    width: 240px;
  }
}
.rail-acc-info {
  min-width: 0;
  flex: 1;
}
.rail-acc-name {
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rail-acc-sub {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 次左侧知乎源列表样式 */
.zhihu-secondary-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.zhihu-sec-header {
  padding: 12px;
  border-bottom: 1px solid var(--border-color);
}
.zhihu-sec-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 8px;
  white-space: nowrap;
  gap: 6px;
}
.zhihu-add-row {
  display: flex;
  gap: 6px;
}
.zhihu-sec-input {
  flex: 1;
  min-width: 0;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--border-color);
  background: var(--bg-input);
  color: var(--text-main);
  font-size: 12px;
}
.zhihu-sub-pills {
  display: flex;
  gap: 4px;
  margin-top: 8px;
  white-space: nowrap;
}
.zhihu-sub-pills button {
  flex: 1;
  min-width: 0;
  padding: 4px 4px;
  font-size: 11px;
  border-radius: 5px;
  border: 1px solid var(--border-color);
  background: var(--bg-input);
  color: var(--text-muted);
  cursor: pointer;
  white-space: nowrap;
}
.zhihu-sub-pills button.active {
  background: var(--sys-tint-blue);
  color: var(--sys-accent-blue);
  border-color: var(--sys-tint-blue-border);
  font-weight: 600;
}
.zhihu-sec-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.zhihu-source-item {
  padding: 8px 10px;
  border-radius: 9px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
  display: flex;
  align-items: center;
  gap: 9px;
  cursor: pointer;
  transition: all 0.15s;
}
.zhihu-source-item:hover {
  background: var(--bg-card-hover);
}
.zhihu-source-item.active {
  border-color: var(--sys-tint-blue-border);
  background: var(--sys-tint-blue);
  box-shadow: var(--shadow-sm);
}
.zhihu-src-avatar, .zhihu-src-avatar-img {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
  object-fit: cover;
}
.zhihu-src-info {
  flex: 1;
  min-width: 0;
}
.zhihu-src-name {
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.zhihu-src-sub {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.btn-src-del {
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  padding: 2px 4px;
  opacity: 0.6;
}
.btn-src-del:hover {
  color: var(--sys-accent-red);
  opacity: 1;
}

/* 中间知乎图文流样式 */
.zhihu-main-toolbar {
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-navbar);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
}
.zhihu-toolbar-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
}
.zhihu-header-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--sys-tint-blue);
  color: var(--sys-accent-blue);
  border: 1px solid var(--sys-tint-blue-border);
}
.zhihu-filter-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
  gap: 10px;
}
.zhihu-filter-pill {
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  color: var(--text-muted);
  cursor: pointer;
}
.zhihu-filter-pill.active {
  background: var(--sys-tint-blue);
  color: var(--sys-accent-blue);
  border-color: var(--sys-tint-blue-border);
  font-weight: 600;
}
.zhihu-cards-container {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}
.zhihu-card-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.zhihu-post-card {
  padding: 14px 16px;
  border-radius: 10px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  transition: border-color 0.15s, background-color 0.15s, box-shadow 0.15s;
}
.zhihu-post-card:hover {
  border-color: var(--border-strong);
  background: var(--bg-card-hover);
}
.zhihu-post-card.selected {
  border-color: var(--sys-accent-blue);
  box-shadow: 0 0 0 1px var(--sys-tint-blue-border), var(--shadow-sm);
}
.zhihu-card-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.zhihu-card-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.zhihu-card-title {
  font-size: 15px;
  font-weight: 700;
  margin: 0;
  color: var(--text-main);
}
.zhihu-type-tag {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 5px;
  font-weight: 600;
  background: var(--sys-tint-blue);
  color: var(--sys-accent-blue);
}
.zhihu-type-tag.article {
  background: var(--sys-tint-purple);
  color: var(--sys-accent-purple);
}
.zhihu-type-tag.upvote {
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
}
.zhihu-new-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--sys-tint-red);
  color: var(--sys-accent-red);
}
.zhihu-card-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.zhihu-card-excerpt {
  font-size: 13px;
  color: var(--text-muted);
  margin: 8px 0;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.zhihu-card-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--text-muted);
  padding-top: 6px;
  border-top: 1px dashed var(--btn-sec-bg);
}

/* 第 4 栏：右侧阅读器与队列面板 */
.right-detail-panel {
  width: 340px;
  background: var(--bg-reader);
  border-left: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  overflow: hidden;
}
.right-panel-tabs {
  padding: 8px;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  gap: 6px;
  background: var(--bg-subtle);
}
.right-tab-btn {
  flex: 1;
  padding: 7px 10px;
  border-radius: 7px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.right-tab-btn.active {
  background: var(--sys-tint-blue);
  color: var(--sys-accent-blue);
  border-color: var(--sys-tint-blue-border);
}
.right-reader-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
}
.reader-meta-box {
  padding-bottom: 12px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
}
.reader-title {
  font-size: 15px;
  font-weight: 700;
  margin: 4px 0 8px;
  line-height: 1.45;
}
.reader-author-line {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 10px;
}
.reader-action-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.reader-rich-content {
  font-size: 13px;
  line-height: 1.75;
  color: var(--text-main);
  word-break: break-word;
}
.reader-rich-content :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: 8px;
  margin: 10px 0;
}
.reader-rich-content :deep(blockquote) {
  border-left: 3px solid var(--sys-accent-blue);
  padding: 6px 12px;
  margin: 10px 0;
  background: var(--bg-secondary);
  color: var(--text-muted);
}
.reader-rich-content :deep(pre) {
  background: var(--bg-secondary);
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
}
.right-queue-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.right-queue-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.right-task-card {
  padding: 10px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
}
.right-audit-logs {
  height: 190px;
  border-top: 1px solid var(--border-color);
  background: var(--bg-input);
  padding: 10px 12px;
  overflow-y: auto;
  font-family: Consolas, monospace;
  font-size: 11px;
}
.audit-log-line {
  display: flex;
  gap: 6px;
  color: var(--text-muted);
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
}
.btn-micro.primary {
  background: var(--sys-accent-blue);
  color: #fff;
  border-color: var(--sys-accent-blue);
}

/* ==================== 平台内嵌登录卡片样式 (置于各平台二级侧边栏顶部) ==================== */
.platform-inline-auth-card {
  margin: 10px 10px 6px;
  padding: 10px;
  border-radius: 10px;
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.platform-inline-auth-card.zhihu {
  border-color: var(--sys-tint-blue-border);
}
.platform-inline-auth-card.bili {
  border-color: var(--sys-tint-pink-border);
}
.inline-auth-top {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
}
.inline-auth-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
}
.inline-auth-avatar-placeholder {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 14px;
  color: #fff;
  flex-shrink: 0;
}
.inline-auth-avatar-placeholder.zhihu {
  background: var(--sys-accent-blue);
}
.inline-auth-avatar-placeholder.bili {
  background: var(--sys-accent-pink);
}
.inline-auth-info {
  flex: 1;
  min-width: 0;
}
.inline-auth-name {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-main);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}
.inline-auth-status {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 99px;
  background: var(--btn-sec-bg);
  color: var(--text-muted);
}
.inline-auth-status.online {
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
}
.inline-auth-sub {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 2px;
}
.inline-auth-actions {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}


/* 平台限流切换与任务细度分组样式 */
.limiter-platform-switcher {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
  background: var(--bg-card);
  padding: 6px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
}
.limiter-plat-btn {
  flex: 1;
  padding: 8px 12px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.limiter-plat-btn:hover {
  color: var(--text-main);
  background: var(--bg-card-hover);
}
.limiter-plat-btn.active {
  background: var(--btn-sec-bg);
  color: var(--text-main);
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}

.task-dimension-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  flex-wrap: wrap;
}
.dimension-label {
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 600;
}
.task-groups-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.task-group-card {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-card);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}
.task-group-header {
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--bg-subtle);
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
}
.group-title-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.group-name {
  font-size: 13px;
  color: var(--text-main);
}
.group-meta-pill {
  font-size: 11px;
  color: var(--text-muted);
  background: var(--bg-card);
  padding: 1px 7px;
  border-radius: 999px;
  border: 1px solid var(--border-color);
}
.group-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.group-task-grid {
  padding: 12px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 10px;
}
.zhihu-update-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--sys-tint-orange);
  color: var(--sys-accent-orange);
  border: 1px solid var(--sys-tint-orange-border);
}

/* ==================== 响应式屏幕断点自适应 (@media) ==================== */
@media (max-width: 1440px) {
  .right-detail-panel {
    width: 340px;
  }
  .navbar-search {
    width: 300px;
    max-width: 40%;
  }
}

@media (max-width: 1200px) {
  .sidebar {
    width: 210px;
  }
  .video-grid {
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 14px;
  }
  .limiter-dashboard-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .preset-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .right-detail-panel {
    width: 310px;
  }
}

@media (max-width: 1024px) {
  .brand-text .sub {
    display: none;
  }
  .brand-text h1 {
    font-size: 15px;
  }
  .navbar-search {
    width: 220px;
  }
  .right-detail-panel {
    position: fixed;
    top: 60px;
    right: 0;
    bottom: 0;
    width: 360px;
    z-index: 90;
    box-shadow: -8px 0 25px rgba(0, 0, 0, 0.4);
  }
  .toolbar {
    padding: 10px 14px;
  }
}

@media (max-width: 768px) {
  .navbar {
    padding: 0 10px;
    gap: 10px;
  }
  .navbar-search {
    display: none;
  }
  .primary-platform-rail {
    width: 56px;
  }
  .sidebar {
    width: 170px;
  }
  .video-grid {
    grid-template-columns: 1fr;
  }
  .group-task-grid {
    grid-template-columns: 1fr;
  }
  .zhihu-card-meta {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
  }
}

/* ==================== 原生桌面无边框与 macOS HIG 深度优化 ==================== */
html, body, #app {
  margin: 0 !important;
  padding: 0 !important;
  width: 100vw !important;
  height: 100vh !important;
  overflow: hidden !important;
  user-select: none;
  -webkit-user-select: none;
}

/* Cleaned up app-layout reset */

/* 顶部原生窗口拖动区 (Tauri Window Dragging) */
.navbar {
  -webkit-app-region: drag;
  user-select: none;
}

.navbar button,
.navbar input,
.navbar .segmented-control,
.navbar .traffic-lights,
.navbar a {
  -webkit-app-region: no-drag;
}

/* 缩小版 macOS 原生质感红绿灯控制 */
.traffic-lights {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 12px;
  -webkit-app-region: no-drag;
}

.traffic-light {
  width: 10px !important;
  height: 10px !important;
  border-radius: 50% !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  border: none !important;
  padding: 0 !important;
  cursor: pointer !important;
  opacity: 0.88;
  transition: all 0.15s ease;
  font-size: 8px;
  line-height: 1;
  color: transparent;
}

.traffic-lights:hover .traffic-light {
  opacity: 1;
}

.traffic-light.close {
  background-color: #FF5F56 !important;
  box-shadow: 0 0 0 0.5px rgba(224, 68, 62, 0.6);
}
.traffic-light.close:hover {
  background-color: #e0443e !important;
}

.traffic-light.min {
  background-color: #FFBD2E !important;
  box-shadow: 0 0 0 0.5px rgba(222, 161, 35, 0.6);
}
.traffic-light.min:hover {
  background-color: #dea123 !important;
}

.traffic-light.max {
  background-color: #27C93F !important;
  box-shadow: 0 0 0 0.5px rgba(26, 171, 41, 0.6);
}
.traffic-light.max:hover {
  background-color: #1aab29 !important;
}

/* 允许正文内容与输入框文字选择 */
input, textarea, .reader-body, .zhihu-html-content, .article-view, pre, code {
  user-select: text !important;
  -webkit-user-select: text !important;
}

/* 顶部自动追更指示灯与按钮 */
.btn-autocheck-top {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 5px 11px;
  border-radius: 6px;
  transition: all 0.2s;
}

.autocheck-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-dim);
  display: inline-block;
  transition: all 0.2s;
}

.autocheck-status-dot.on {
  background: var(--sys-accent-green);
  box-shadow: 0 0 6px rgba(16, 185, 129, 0.5);
}

.autocheck-status-dot.checking {
  animation: pulseDot 1s infinite alternate;
}

@keyframes pulseDot {
  0% { transform: scale(0.9); opacity: 0.6; }
  100% { transform: scale(1.3); opacity: 1; }
}

.autocheck-tag-pulse {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
  font-weight: 600;
}

.btn-refresh-top {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  padding: 5px 10px;
}

.rail-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 6px;
}

.btn-micro-refresh {
  background: transparent;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.btn-micro-refresh:hover {
  background: var(--bg-card-hover);
  color: var(--text-main);
  transform: rotate(45deg);
}


/* ==================== 刷新与爬取快捷控制胶囊 & 白昼模式高对比度修正 ==================== */
.refresh-scrape-pills {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border-radius: 8px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-color);
}
.rs-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}
.rs-pill:hover {
  color: var(--text-main);
  background: var(--bg-card-hover);
}
.rs-pill.active {
  background: var(--bg-card);
  color: var(--text-main);
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}
.rs-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-dim);
  flex-shrink: 0;
  transition: all 0.2s ease;
}
.rs-dot.on {
  background: var(--sys-accent-green);
  box-shadow: 0 0 5px rgba(16, 185, 129, 0.55);
}

/* 确保二级侧边栏顶部账号切换条永不换行 */
.inline-auth-top {
  white-space: nowrap;
}
.inline-auth-name {
  white-space: nowrap;
  overflow: hidden;
}
.inline-auth-status,
.vip-tag,
.inline-auth-top .btn-micro {
  white-space: nowrap !important;
  flex-shrink: 0 !important;
}

/* 白天模式 (Light Mode) 表面细节高对比度微调 */
.app-layout[data-theme="light"] .zhihu-post-card,
.app-layout[data-theme="light"] .video-card,
.app-layout[data-theme="light"] .setting-card,
.app-layout[data-theme="light"] .task-group-card,
.app-layout[data-theme="light"] .task-item-card,
.app-layout[data-theme="light"] .right-task-card {
  border-color: rgba(60, 60, 67, 0.08);
}

/* ======================================================================
   全屏系统设置中心 (Fullscreen Settings Center)
   按照 Google Material 3 与 Apple HIG 规范实现
====================================================================== */
.settings-fullscreen-view {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: var(--bg-main);
  overflow: hidden;
}

.settings-top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 28px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  gap: 16px;
}

.settings-bar-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.btn-back-workbench {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 6px 12px;
  border-radius: 6px;
  font-weight: 500;
  white-space: nowrap !important;
  flex-shrink: 0 !important;
}
.settings-bar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.settings-bar-right .btn-primary {
  white-space: nowrap !important;
  flex-shrink: 0 !important;
}

.settings-title-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.settings-main-title {
  font-size: 16px;
  font-weight: 700;
  margin: 0;
  color: var(--text-main);
}

.settings-main-subtitle {
  font-size: 11px;
  color: var(--text-muted);
}

.settings-bar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.settings-save-toast {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--sys-accent-green);
  font-weight: 600;
  padding: 4px 10px;
  background: var(--sys-tint-green);
  border-radius: 6px;
  animation: fadeIn 0.2s ease-in-out;
}

.settings-content-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
}

.settings-container-max {
  max-width: 920px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.settings-secondary-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-secondary);
}

.settings-sec-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-main);
}

.settings-subnav-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 8px;
}

.settings-subnav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
  position: relative;
}

.settings-subnav-item:hover {
  background: var(--bg-hover);
}

.settings-subnav-item.active {
  background: var(--sys-tint-blue);
  border-color: var(--sys-tint-blue-border);
}

.settings-subnav-item.active .subnav-title {
  color: var(--color-primary);
  font-weight: 700;
}

.settings-subnav-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  background: var(--bg-card);
  color: var(--text-main);
  flex-shrink: 0;
  border: 1px solid var(--border-color);
}

.settings-subnav-item.active .settings-subnav-icon {
  background: var(--color-primary);
  color: #fff;
  border-color: var(--color-primary);
}

.settings-subnav-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.subnav-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
}

.subnav-desc {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.settings-update-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--sys-accent-red);
  margin-left: auto;
  box-shadow: 0 0 6px rgba(239, 68, 68, 0.6);
  flex-shrink: 0;
}

.rail-update-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--sys-accent-red);
  margin-left: auto;
  margin-right: 4px;
  box-shadow: 0 0 6px rgba(239, 68, 68, 0.6);
  flex-shrink: 0;
}

/* 关于与更新模块卡片样式 */
.about-hero-card {
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.08) 0%, rgba(99, 102, 241, 0.05) 100%) !important;
  border: 1px solid rgba(37, 99, 235, 0.25) !important;
  border-radius: 12px;
  padding: 22px;
}

.about-hero-header {
  display: flex;
  align-items: center;
  gap: 16px;
}

.about-hero-logo {
  width: 50px;
  height: 50px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--sys-accent-blue), var(--sys-accent-indigo));
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 14px var(--sys-tint-blue-border);
  flex-shrink: 0;
}

.about-hero-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.about-hero-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.about-hero-title {
  font-size: 19px;
  font-weight: 800;
  margin: 0;
  color: var(--text-main);
  letter-spacing: -0.2px;
}

.about-ver-tag {
  font-size: 11px;
  font-weight: 700;
  color: var(--color-primary);
  background: var(--sys-tint-blue);
  padding: 2px 9px;
  border-radius: 9999px;
  border: 1px solid var(--sys-tint-blue-border);
}

.about-license-tag {
  font-size: 11px;
  color: var(--text-muted);
  background: var(--bg-card);
  padding: 2px 9px;
  border-radius: 9999px;
  border: 1px solid var(--border-color);
}

.about-hero-slogan {
  font-size: 12px;
  color: var(--text-muted);
  margin: 2px 0 0 0;
}

/* 检查更新区域样式 */
.update-status-box {
  border-radius: 8px;
  padding: 16px;
  background: var(--bg-input);
  border: 1px solid var(--border-color);
}

.update-checking-state {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-muted);
}

.update-found-state {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.update-found-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.update-alert-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 700;
  color: var(--sys-accent-orange);
  background: var(--sys-tint-orange);
  padding: 4px 10px;
  border-radius: 6px;
  border: 1px solid var(--sys-tint-orange-border);
}

.update-date-text {
  font-size: 11px;
  color: var(--text-muted);
}

.update-release-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-main);
  margin: 0;
}

.update-changelog-view {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.changelog-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
}

.changelog-content {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 12px 14px;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-main);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow-y: auto;
  margin: 0;
}

.update-action-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.update-latest-state {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.latest-badge-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--sys-accent-green);
  font-weight: 700;
  font-size: 14px;
}

.latest-icon {
  display: flex;
  align-items: center;
}

.latest-hint-text {
  font-size: 12px;
  color: var(--text-muted);
  margin: 2px 0 0 0;
}

.update-fallback-state {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.fallback-status-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
}

.update-err-hint {
  font-size: 11px;
  color: var(--text-muted);
  margin: 2px 0 0 0;
}

/* 开源链接网格 */
.about-links-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.about-link-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: var(--bg-input);
  cursor: pointer;
  transition: all 0.15s ease;
}

.about-link-item:hover {
  border-color: var(--color-primary);
  background: var(--bg-hover);
  transform: translateY(-1px);
}

.link-item-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.link-item-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--sys-tint-blue);
  color: var(--color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.link-item-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
}

.link-item-desc {
  font-size: 11px;
  color: var(--text-muted);
}

.link-arrow-icon {
  color: var(--text-muted);
  transition: color 0.15s ease;
}

.about-link-item:hover .link-arrow-icon {
  color: var(--color-primary);
}

/* 运行环境与诊断表格 */
.about-env-table {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.env-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--bg-input);
  border: 1px solid var(--border-color);
  gap: 12px;
}

.env-label {
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  flex-shrink: 0;
}

.env-value {
  color: var(--text-main);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
  font-size: 12px;
  text-align: right;
  word-break: break-all;
}

/* ======================================================================
   软件全局原生圆角窗口与 M3 高级视觉规范
====================================================================== */
.app-layout {
  width: 100vw !important;
  height: 100vh !important;
  box-sizing: border-box !important;
  border-radius: 0 !important;
  overflow: hidden !important;
  border: 1px solid var(--border-color) !important;
  box-shadow: none !important;
  background-color: var(--bg-main) !important;
}

.app-layout.is-maximized {
  border-radius: 0 !important;
  border: none !important;
  box-shadow: none !important;
}

/* 顶部日夜模式单按钮 */
.btn-theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
  padding: 5px 10px;
  border-radius: 6px;
  background: var(--bg-input);
  color: var(--text-main);
  border: 1px solid var(--border-color);
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-theme-toggle:hover {
  background: var(--bg-hover);
  border-color: var(--color-primary);
  color: var(--color-primary);
}

/* ======================================================================
   关于与更新 M3 顶级美学界面规范 (About & Update Redesign)
====================================================================== */
.about-redesign-pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.m3-hero-banner {
  position: relative;
  border-radius: 14px;
  overflow: hidden;
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.09) 0%, rgba(99, 102, 241, 0.07) 100%);
  border: 1px solid rgba(37, 99, 235, 0.22);
  box-shadow: 0 4px 20px rgba(37, 99, 235, 0.08);
  padding: 24px 28px;
}

.m3-hero-content {
  display: flex;
  align-items: center;
  gap: 20px;
  position: relative;
  z-index: 1;
}

.m3-brand-crest {
  flex-shrink: 0;
}

.crest-icon-wrap {
  width: 58px;
  height: 58px;
  border-radius: 16px;
  background: linear-gradient(135deg, var(--sys-accent-blue), var(--sys-accent-indigo));
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 24px var(--sys-tint-blue-border);
}

.m3-brand-details {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.m3-brand-header-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.m3-brand-title {
  font-size: 22px;
  font-weight: 800;
  margin: 0;
  color: var(--text-main);
  letter-spacing: -0.4px;
}

.m3-chip {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 9999px;
}

.m3-chip-primary {
  background: var(--sys-tint-blue);
  color: var(--color-primary);
  border: 1px solid var(--sys-tint-blue-border);
}

.m3-chip-tonal {
  background: var(--bg-card);
  color: var(--text-muted);
  border: 1px solid var(--border-color);
}

.m3-chip-outline {
  background: transparent;
  color: var(--sys-accent-green);
  border: 1px solid var(--sys-tint-green-border);
}

.m3-brand-caption {
  font-size: 12px;
  color: var(--text-muted);
  margin: 0;
  line-height: 1.5;
}

.m3-card-elevated {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.03);
}

.m3-card-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.m3-title-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
}

.m3-section-icon {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: var(--sys-tint-blue);
  color: var(--color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.m3-update-console {
  margin-top: 16px;
  border-radius: 10px;
  padding: 16px 20px;
  background: var(--bg-input);
  border: 1px solid var(--border-color);
}

.m3-console-state {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.m3-console-state.checking {
  flex-direction: row;
  align-items: center;
  gap: 14px;
  padding: 8px 0;
}

.m3-loading-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--sys-tint-blue);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.state-msg-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.state-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
}

.state-sub {
  font-size: 11px;
  color: var(--text-muted);
}

.update-pulse-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.update-tag-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 700;
  color: var(--sys-accent-orange);
  background: var(--sys-tint-orange);
  padding: 4px 12px;
  border-radius: 9999px;
  border: 1px solid var(--sys-tint-orange-border);
}

.status-pulse-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--sys-accent-orange);
  box-shadow: 0 0 8px rgba(245, 158, 11, 0.8);
  animation: pulse 1.6s infinite;
}

.update-meta-text {
  font-size: 11px;
  color: var(--text-muted);
}

.update-headline {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-main);
  margin: 6px 0 0 0;
}

.m3-changelog-terminal {
  margin-top: 10px;
  border-radius: 8px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
  overflow: hidden;
}

.terminal-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
}

.terminal-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-primary);
}

.terminal-title {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.terminal-text {
  padding: 12px 14px;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", Consolas, monospace;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-main);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 180px;
  overflow-y: auto;
  margin: 0;
}

.update-buttons-bar {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.uptodate-badge-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.uptodate-icon-box {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.uptodate-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--sys-accent-green);
}

.uptodate-desc {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
}

.standby-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.standby-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--btn-sec-bg);
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.standby-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
}

.standby-desc {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
}

.m3-toggle-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--border-color);
}

.toggle-title {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
}

.toggle-desc {
  display: block;
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
}

.m3-cards-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}

.m3-interactive-card {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 16px 18px;
  border-radius: 12px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.m3-interactive-card:not(.plain):hover {
  border-color: var(--color-primary);
  transform: translateY(-2px);
  box-shadow: 0 6px 18px var(--sys-tint-blue);
}

.m3-interactive-card.plain {
  cursor: default;
}

.card-icon-halo {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: var(--sys-tint-blue);
  color: var(--color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.card-icon-halo.emerald {
  background: var(--sys-tint-green);
  color: var(--sys-accent-green);
}

.card-text-body {
  flex: 1;
  min-width: 0;
}

.card-headline-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-headline {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-main);
  margin: 0;
}

.external-glyph {
  color: var(--text-muted);
  transition: transform 0.15s, color 0.15s;
}

.m3-interactive-card:hover .external-glyph {
  color: var(--color-primary);
  transform: translate(1px, -1px);
}

.chip-status-safe {
  font-size: 10px;
  font-weight: 700;
  color: var(--sys-accent-green);
  background: var(--sys-tint-green);
  padding: 2px 7px;
  border-radius: 4px;
}

.card-caption {
  font-size: 11px;
  color: var(--text-muted);
  margin: 4px 0 6px 0;
  line-height: 1.4;
}

.card-sub-code {
  font-size: 11px;
  color: var(--color-primary);
  font-family: Consolas, monospace;
}

.m3-specs-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
}

.m3-spec-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--bg-input);
  border: 1px solid var(--border-color);
  gap: 12px;
}

.m3-spec-item.full-width {
  grid-column: 1 / -1;
}

.spec-prop {
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  flex-shrink: 0;
}

.spec-val {
  color: var(--text-main);
  font-size: 12px;
  font-weight: 600;
  text-align: right;
}

.highlight-bili {
  color: var(--sys-accent-pink);
}

.highlight-zhihu {
  color: var(--sys-accent-blue);
}

.spec-code {
  color: var(--text-main);
  font-family: Consolas, monospace;
  font-size: 11px;
  padding: 2px 6px;
  background: var(--bg-card);
  border-radius: 4px;
  border: 1px solid var(--border-color);
}

/* UP主聚焦与合集监听控制栏 */
.up-focus-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background: var(--bg-card);
  border-bottom: 1px solid var(--border-color);
  gap: 12px;
  flex-wrap: wrap;
}
.up-focus-info {
  display: flex;
  align-items: center;
  gap: 10px;
}
.up-avatar-mini img {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  object-fit: cover;
  border: 1px solid var(--border-color);
}
.up-meta-text {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.up-title-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-main);
}
.up-collection-hint {
  font-size: 12px;
  color: var(--text-secondary);
}
.up-focus-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.btn-collections-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
}
.btn-collections-toggle.active {
  background: var(--sys-tint-blue);
  color: var(--sys-accent-blue);
  border-color: var(--sys-tint-blue-border);
}
.toggle-arrow {
  font-size: 10px;
  opacity: 0.7;
}

/* UP主合集抽屉/折叠面板 */
.up-collections-panel {
  background: var(--bg-subtle);
  border-bottom: 2px solid var(--border-color);
  padding: 16px 20px;
  box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.04);
}
.collections-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 14px;
  gap: 12px;
}
.collections-header-left h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-main);
}
.collections-header-left p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}
.collections-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.collections-loading, .collections-empty {
  padding: 24px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.collections-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.col-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: all 0.2s ease;
}
.col-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}
.col-cover-box {
  position: relative;
  width: 100%;
  height: 120px;
  background: var(--bg-subtle);
  overflow: hidden;
}
.col-cover {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.col-cover-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: var(--text-dim);
}
.col-type-tag {
  position: absolute;
  top: 6px;
  left: 6px;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 4px;
  backdrop-filter: blur(4px);
}
.col-type-tag.season {
  background: rgba(0, 122, 255, 0.85);
}
.col-type-tag.series {
  background: rgba(255, 149, 0, 0.85);
}
.col-ep-count {
  position: absolute;
  bottom: 6px;
  right: 6px;
  background: rgba(0, 0, 0, 0.75);
  color: #fff;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
}
.col-body {
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}
.col-title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text-main);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.col-controls {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-top: auto;
}
.col-monitor-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  font-size: 12px;
  user-select: none;
}
.col-monitor-switch input {
  display: none;
}
.col-monitor-switch .switch-slider {
  width: 28px;
  height: 16px;
  background-color: var(--border-strong);
  border-radius: 16px;
  position: relative;
  transition: 0.2s;
}
.col-monitor-switch .switch-slider:before {
  content: "";
  position: absolute;
  width: 12px;
  height: 12px;
  left: 2px;
  top: 2px;
  background-color: white;
  border-radius: 50%;
  transition: 0.2s;
}
.col-monitor-switch input:checked + .switch-slider {
  background-color: var(--sys-accent-green);
}
.col-monitor-switch input:checked + .switch-slider:before {
  transform: translateX(12px);
}
.col-monitor-switch .switch-label {
  font-size: 11px;
  color: var(--text-secondary);
}

.up-paused-tag {
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--sys-tint-orange);
  color: var(--sys-accent-orange);
  margin-left: 4px;
}

/* 盐选徽章与禁用按钮 */
.zhihu-yanxuan-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  background: var(--sys-tint-red);
  color: var(--sys-accent-red);
  border: 1px solid var(--sys-tint-red-border);
  font-size: 11px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
}
.btn-micro.disabled-paid {
  background: var(--sys-tint-red);
  color: var(--sys-accent-red);
  border: 1px solid var(--sys-tint-red-border);
  opacity: 0.8;
  cursor: not-allowed;
}

/* 免责声明弹窗 */
.disclaimer-modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 99999;
}
.disclaimer-modal-card {
  width: 620px;
  max-width: 90vw;
  max-height: 85vh;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 14px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.35);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: modalPop 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes modalPop {
  0% { transform: scale(0.95); opacity: 0; }
  100% { transform: scale(1); opacity: 1; }
}
.disclaimer-modal-header {
  padding: 18px 24px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-subtle);
}
.disclaimer-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.disclaimer-icon {
  font-size: 28px;
  line-height: 1;
}
.disclaimer-modal-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--text-main);
}
.disclaimer-subtitle {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}
.disclaimer-modal-body {
  padding: 20px 24px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-main);
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.disclaimer-section h4 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-main);
}
.disclaimer-section p {
  margin: 0;
  color: var(--text-secondary);
}
.disclaimer-section.warning-box {
  background: var(--sys-tint-orange);
  border: 1px solid var(--sys-tint-orange-border);
  padding: 12px 14px;
  border-radius: 8px;
}
.disclaimer-section.warning-box h4 {
  color: var(--sys-accent-orange);
}
.disclaimer-section.warning-box ul {
  margin: 6px 0 0 16px;
  padding: 0;
  color: var(--text-main);
}
.disclaimer-section.warning-box li {
  margin-bottom: 4px;
}
.disclaimer-modal-footer {
  padding: 14px 24px;
  border-top: 1px solid var(--border-color);
  background: var(--bg-subtle);
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
.disclaimer-btn-reject {
  background: var(--btn-sec-bg);
  color: var(--text-secondary);
  border: 1px solid var(--border-color);
}
.disclaimer-btn-reject:hover {
  background: var(--sys-tint-red);
  color: var(--sys-accent-red);
  border-color: var(--sys-tint-red-border);
}
.disclaimer-btn-accept {
  font-weight: 600;
}

/* 折叠诊断信息卡片 */
.diagnostics-details summary {
  user-select: none;
  outline: none;
  display: flex;
  align-items: center;
}
.diagnostics-details summary::-webkit-details-marker,
.diagnostics-details summary::marker {
  display: none;
}
.diagnostics-details[open] summary {
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
}
/* 顶部工具栏轻量下拉菜单 */
.toolbar-dropdown-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 90;
  min-width: 170px;
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.25);
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.dropdown-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--text-main);
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s ease;
}

.dropdown-menu-item:hover:not(:disabled) {
  background: var(--bg-hover);
}

.dropdown-menu-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ==================== 软件内更新下载进度与自动更新弹窗 ==================== */
.update-modal-card {
  max-width: 560px;
}
.update-matched-pkg {
  font-size: 12.5px;
  color: var(--text-secondary);
  background: var(--bg-tertiary, rgba(128, 128, 128, 0.08));
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
}
.update-matched-pkg strong {
  color: var(--text-primary);
}
.update-modal-changelog {
  margin: 6px 0 0;
  padding: 10px 12px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}
.update-dl-progress-box {
  margin: 12px 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--bg-tertiary, rgba(59, 130, 246, 0.06));
  border: 1px solid var(--border-color);
}
.update-dl-progress-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 6px;
  gap: 8px;
}
.update-dl-track {
  width: 100%;
  height: 8px;
  background: rgba(128, 128, 128, 0.2);
  border-radius: 999px;
  overflow: hidden;
}
.update-dl-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #10b981);
  border-radius: 999px;
  transition: width 0.25s ease;
}
.update-notice-tip {
  font-size: 12px;
  color: #10b981;
  margin: 8px 0;
}
.update-error-tip {
  font-size: 12px;
  color: #ef4444;
  margin: 8px 0;
}
.update-modal-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* ==================== 全局无阻塞轻量通知 (成功绿色 / 失败红色) ==================== */
.gb-toast-container {
  position: fixed;
  top: 52px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 100000;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  pointer-events: none;
}
.gb-toast-item {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 9px 16px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  max-width: 520px;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.28);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}
.gb-toast-item.is-success {
  background: rgba(16, 185, 129, 0.92);
  border: 1px solid rgba(52, 211, 153, 0.65);
  color: #ffffff;
}
.gb-toast-item.is-error {
  background: rgba(239, 68, 68, 0.94);
  border: 1px solid rgba(248, 113, 113, 0.65);
  color: #ffffff;
}
.gb-toast-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.gb-toast-text {
  word-break: break-word;
}
.gb-toast-enter-active,
.gb-toast-leave-active {
  transition: all 0.24s cubic-bezier(0.16, 1, 0.3, 1);
}
.gb-toast-enter-from,
.gb-toast-leave-to {
  opacity: 0;
  transform: translateY(-12px) scale(0.96);
}
</style>
