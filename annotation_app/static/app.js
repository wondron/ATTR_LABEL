(function () {
  "use strict";

  const API_ROOT = "/api/v1";
  const IMAGE_SYNC_DEBOUNCE_MS = 80;
  const IMAGE_EVENT_RECONNECT_BASE_MS = 1200;
  const IMAGE_EVENT_RECONNECT_MAX_MS = 15000;
  const IMAGE_CALIBRATION_CONNECTED_MS = 15000;
  const IMAGE_CALIBRATION_FALLBACK_MS = 2000;

  const DEFAULT_OPTIONS = Object.freeze({
    container_type: [
      "无", "玻璃容器", "塑料容器", "陶瓷容器", "泡沫容器", "金属容器",
      "木质/竹制容器", "纸质容器", "油纸", "珐琅锅", "保鲜膜", "铝箔纸"
    ],
    accessory_type: [
      "无", "玻璃蒸烤盘", "脆烤盘", "微波专用烤架", "烤架", "炸烤网架",
      "烤盘", "有孔蒸盘", "无孔蒸盘", "小炸篮", "转轴烤叉", "炸烤盘"
    ],
    rack_level: ["0", "1", "2", "3", "4", "5", "无"],
    device_model: ["C9277A", "CQ09-i9", "C87-i7Pro", "DB677"]
  });

  const state = {
    config: null,
    options: cloneOptions(DEFAULT_OPTIONS),
    images: [],
    filteredImages: [],
    counts: { total: 0, labeled: 0, unlabeled: 0, invalid: 0 },
    currentImage: null,
    annotationExists: false,
    annotationInvalid: false,
    revision: null,
    draft: createEmptyAnnotations(null),
    dirty: false,
    editRevision: 0,
    loadingAnnotation: false,
    saving: false,
    deleting: false,
    downloading: false,
    imageListRevision: 0,
    imageStateUncertain: false,
    previewRetryTimer: null,
    previewRetries: 0,
    switchingFolder: false,
    browsingFolder: false,
    statisticsLoading: false,
    statisticsSequence: 0,
    directoryGeneration: null,
    directorySwitchGeneration: null,
    directoryBrowseSequence: 0,
    directoryBrowseMode: "roots",
    directoryBrowsePath: "",
    directoryBrowseParentPath: null,
    directorySelectedPath: "",
    directoryStale: false,
    loadSequence: 0,
    zoomFactor: 1,
    fitScale: 1,
    draftImageIds: new Set(),
    imageEventSource: null,
    imageEventGeneration: null,
    imageEventConnected: false,
    imageEventFailures: 0,
    imageEventReconnectTimer: null,
    imageSyncDebounceTimer: null,
    imageCalibrationTimer: null,
    imageSyncInFlight: false,
    imageSyncPending: false,
    imageSyncAutoSelect: false
  };

  const elements = {
    rootPath: byId("rootPath"),
    totalCount: byId("totalCount"),
    labeledCount: byId("labeledCount"),
    unlabeledCount: byId("unlabeledCount"),
    invalidCount: byId("invalidCount"),
    invalidStat: byId("invalidStat"),
    serviceStatus: byId("serviceStatus"),
    openStatisticsButton: byId("openStatisticsButton"),
    chooseDataDirButton: byId("chooseDataDirButton"),
    chooseDataDirButtonText: byId("chooseDataDirButtonText"),
    folderSwitchStatus: byId("folderSwitchStatus"),
    dataDirectoryDialog: byId("dataDirectoryDialog"),
    dataDirectoryForm: byId("dataDirectoryForm"),
    dataDirectoryPathInput: byId("dataDirectoryPathInput"),
    dataDirectoryGoButton: byId("dataDirectoryGoButton"),
    dataDirectoryUpButton: byId("dataDirectoryUpButton"),
    dataDirectoryRefreshButton: byId("dataDirectoryRefreshButton"),
    dataDirectoryBreadcrumbs: byId("dataDirectoryBreadcrumbs"),
    dataDirectoryBrowser: byId("dataDirectoryBrowser"),
    dataDirectoryLoading: byId("dataDirectoryLoading"),
    dataDirectoryEmpty: byId("dataDirectoryEmpty"),
    dataDirectoryList: byId("dataDirectoryList"),
    browseDataDirectoryText: byId("browseDataDirectoryText"),
    selectedDataDirectoryText: byId("selectedDataDirectoryText"),
    currentDataDirectoryText: byId("currentDataDirectoryText"),
    allowedDataRootsText: byId("allowedDataRootsText"),
    dataDirectoryError: byId("dataDirectoryError"),
    cancelDataDirectoryButton: byId("cancelDataDirectoryButton"),
    cancelDataDirectoryIcon: byId("cancelDataDirectoryIcon"),
    confirmDataDirectoryButton: byId("confirmDataDirectoryButton"),
    confirmDataDirectoryButtonText: byId("confirmDataDirectoryButtonText"),
    visibleCount: byId("visibleCount"),
    search: byId("imageSearch"),
    filter: byId("statusFilter"),
    imageList: byId("imageList"),
    listPlaceholder: byId("listPlaceholder"),
    currentFileName: byId("currentFileName"),
    positionText: byId("positionText"),
    annotationState: byId("annotationState"),
    annotationVersionBadge: byId("annotationVersionBadge"),
    imageStage: byId("imageStage"),
    emptyView: byId("emptyView"),
    viewerLoading: byId("viewerLoading"),
    previewImage: byId("previewImage"),
    imageError: byId("imageError"),
    zoomToolbar: byId("zoomToolbar"),
    zoomText: byId("zoomText"),
    zoomOut: byId("zoomOutButton"),
    zoomIn: byId("zoomInButton"),
    fitButton: byId("fitButton"),
    previousButton: byId("previousButton"),
    nextButton: byId("nextButton"),
    deleteImageButton: byId("deleteImageButton"),
    downloadAnnotatedButton: byId("downloadAnnotatedButton"),
    statusBanner: byId("statusBanner"),
    statusBannerTitle: byId("statusBannerTitle"),
    statusBannerText: byId("statusBannerText"),
    form: byId("annotationForm"),
    fieldset: byId("annotationFields"),
    foodName: byId("foodNameInput"),
    foodCount: byId("foodCountInput"),
    quality: byId("qualityInput"),
    deviceModel: byId("deviceModelSelect"),
    containerChoices: byId("containerChoices"),
    accessoryChoices: byId("accessoryChoices"),
    rackChoices: byId("rackChoices"),
    foodSize: byId("foodSizeInput"),
    saveState: byId("saveState"),
    saveButton: byId("saveButton"),
    saveNextButton: byId("saveNextButton"),
    statisticsDialog: byId("statisticsDialog"),
    closeStatisticsButton: byId("closeStatisticsButton"),
    refreshStatisticsButton: byId("refreshStatisticsButton"),
    refreshStatisticsButtonText: byId("refreshStatisticsButtonText"),
    statisticsOverview: byId("statisticsOverview"),
    statisticsValidCount: byId("statisticsValidCount"),
    statisticsLabeledDetail: byId("statisticsLabeledDetail"),
    statisticsCoverage: byId("statisticsCoverage"),
    statisticsTotalDetail: byId("statisticsTotalDetail"),
    statisticsUnlabeledCount: byId("statisticsUnlabeledCount"),
    statisticsInvalidCount: byId("statisticsInvalidCount"),
    statisticsContext: byId("statisticsContext"),
    statisticsUpdatedAt: byId("statisticsUpdatedAt"),
    statisticsStatus: byId("statisticsStatus"),
    statisticsContent: byId("statisticsContent"),
    toastRegion: byId("toastRegion")
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function cloneOptions(source) {
    return Object.fromEntries(
      Object.entries(source).map(([key, values]) => [key, Array.from(values)])
    );
  }

  function createEmptyAnnotations(config) {
    const builtInDefaults = {
      food_name: "无",
      food_count: null,
      quality: null,
      device_model: null,
      container_type: ["无"],
      accessory_type: ["无"],
      rack_level: ["无"],
      food_size: null
    };
    const definitions = Array.isArray(config && config.fields) ? config.fields : [];
    const configured = {};
    for (const [field, fallback] of Object.entries(builtInDefaults)) {
      const definition = definitions.find((item) => item && item.name === field);
      const value = definition && Object.prototype.hasOwnProperty.call(definition, "default")
        ? definition.default
        : fallback;
      configured[field] = Array.isArray(value) ? Array.from(value) : value;
    }
    return normaliseAnnotations(configured);
  }

  function imageKey(value) {
    return String(value == null ? "" : value);
  }

  function currentImageKey() {
    return state.currentImage ? imageKey(state.currentImage.id) : "";
  }

  function parseDirectoryGeneration(payload) {
    if (!payload || payload.directory_generation == null) return null;
    const value = Number(payload.directory_generation);
    return Number.isInteger(value) && value >= 0 ? value : null;
  }

  function withDirectoryGeneration(path) {
    if (state.directoryGeneration == null) return path;
    const separator = path.includes("?") ? "&" : "?";
    return `${path}${separator}directory_generation=${encodeURIComponent(state.directoryGeneration)}`;
  }

  function isDirectoryChangedError(error) {
    return Boolean(error && error.code === "data_directory_changed");
  }

  function markDirectoryStale(detail) {
    const wasStale = state.directoryStale;
    state.directoryStale = true;
    stopImageRealtimeSync();
    if (elements.dataDirectoryDialog.open) {
      state.directoryBrowseSequence += 1;
      state.browsingFolder = false;
      elements.dataDirectoryDialog.close();
      document.body.classList.remove("dialog-open");
      state.directorySwitchGeneration = null;
      state.directorySelectedPath = "";
    }
    const dataDir = detail && detail.data_dir ? String(detail.data_dir) : "";
    setAnnotationState("error", "目录已切换");
    setSaveState("error", "当前页面已过期，不会读取或保存到其他目录");
    showBanner(
      "warning",
      "数据目录已在其他页面切换",
      "当前表单内容仍保留，但不能继续保存。请点击“切换文件夹”重新选择，或刷新页面。"
    );
    if (!wasStale) {
      showToast(
        "当前页面已停止读写",
        dataDir ? `服务当前使用：${dataDir}` : "请重新选择文件夹或刷新页面。",
        "warning",
        9000
      );
    }
    updateControls();
  }

  function extractErrorMessage(payload, fallback) {
    if (!payload) return fallback;
    if (typeof payload === "string") return payload;
    if (typeof payload.message === "string") return payload.message;
    if (typeof payload.error === "string") return payload.error;
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) {
      return payload.detail.map((item) => item && item.msg ? item.msg : String(item)).join("；");
    }
    if (payload.detail && typeof payload.detail.message === "string") {
      return payload.detail.message;
    }
    return fallback;
  }

  async function apiRequest(path, options) {
    const response = await fetch(`${API_ROOT}${path}`, {
      cache: "no-store",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options && options.body ? { "Content-Type": "application/json" } : {}),
        ...((options && options.headers) || {})
      }
    });

    const contentType = response.headers.get("content-type") || "";
    let payload = null;
    if (contentType.includes("application/json")) {
      try {
        payload = await response.json();
      } catch (_error) {
        payload = null;
      }
    } else {
      const text = await response.text();
      payload = text || null;
    }

    if (!response.ok) {
      const error = new Error(extractErrorMessage(payload, `请求失败（HTTP ${response.status}）`));
      error.status = response.status;
      error.payload = payload;
      const detail = payload && payload.detail;
      error.code = detail && typeof detail === "object" ? detail.code : (payload && payload.code);
      throw error;
    }
    return payload;
  }

  function setServiceStatus(mode, text) {
    elements.serviceStatus.classList.remove("service-status--online", "service-status--offline");
    if (mode === "online") elements.serviceStatus.classList.add("service-status--online");
    if (mode === "offline") elements.serviceStatus.classList.add("service-status--offline");
    elements.serviceStatus.querySelector("span").textContent = text;
  }

  function resolveOptions(config) {
    const sources = [
      config && config.enums,
      config && config.options,
      config && config.attributes,
      config
    ].filter(Boolean);

    function findArray(field, aliases) {
      const fieldConfig = Array.isArray(config && config.fields)
        ? config.fields.find((item) => item && item.name === field)
        : null;
      if (fieldConfig && Array.isArray(fieldConfig.options) && fieldConfig.options.length) {
        return fieldConfig.options;
      }
      for (const source of sources) {
        for (const name of [field, ...(aliases || [])]) {
          const candidate = source[name];
          if (Array.isArray(candidate) && candidate.length) return candidate;
          if (candidate && Array.isArray(candidate.options) && candidate.options.length) {
            return candidate.options;
          }
        }
      }
      return DEFAULT_OPTIONS[field];
    }

    state.options = {
      container_type: normaliseOptionList(findArray("container_type", ["container_types"]), true),
      accessory_type: normaliseOptionList(findArray("accessory_type", ["accessory_types"]), true),
      rack_level: normaliseOptionList(findArray("rack_level", ["rack_levels", "rack_level"]), true),
      device_model: normaliseOptionList(findArray("device_model", ["device_models"]), false)
    };
  }

  function renderSchemaContract() {
    const config = state.config || {};
    const version = String(config.annotation_version || "1.6").trim() || "1.6";
    elements.annotationVersionBadge.textContent = `JSON v${version}`;

    const definitions = Array.isArray(config.fields) ? config.fields : [];
    for (const definition of definitions) {
      if (!definition || !definition.name) continue;
      const typeNode = document.querySelector(`[data-json-type="${definition.name}"]`);
      if (typeNode && definition.json_type) {
        typeNode.textContent = String(definition.json_type);
      }
      const defaultNode = document.querySelector(`[data-json-default="${definition.name}"]`);
      if (defaultNode && Object.prototype.hasOwnProperty.call(definition, "default")) {
        defaultNode.textContent = JSON.stringify(definition.default);
      }
    }
  }

  function renderDeviceModelOptions(selectedValue) {
    const selected = selectedValue == null ? "" : String(selectedValue);
    const fragment = document.createDocumentFragment();
    const emptyOption = document.createElement("option");
    emptyOption.value = "";
    emptyOption.textContent = "未指定（保存为 null）";
    fragment.append(emptyOption);

    for (const value of state.options.device_model) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      fragment.append(option);
    }
    if (selected && !state.options.device_model.includes(selected)) {
      const invalidOption = document.createElement("option");
      invalidOption.value = selected;
      invalidOption.textContent = `${selected}（不在当前配置中）`;
      fragment.append(invalidOption);
    }

    elements.deviceModel.replaceChildren(fragment);
    elements.deviceModel.value = selected;
  }

  function normaliseOptionList(values, includeNone) {
    const result = [];
    for (const value of values || []) {
      const stringValue = String(value).trim();
      if (stringValue && !result.includes(stringValue)) result.push(stringValue);
    }
    if (includeNone) {
      const noneIndex = result.indexOf("无");
      if (noneIndex >= 0) result.splice(noneIndex, 1);
      result.unshift("无");
    }
    return result;
  }

  async function loadConfig() {
    try {
      const config = await apiRequest("/config");
      state.config = config || {};
      state.directoryGeneration = parseDirectoryGeneration(state.config);
      state.directoryStale = false;
      resolveOptions(state.config);
      renderSchemaContract();
      renderDeviceModelOptions(state.draft.device_model);
      const root = state.config.image_root || state.config.root || state.config.image_dir || state.config.data_dir || "10-temp_label";
      elements.rootPath.textContent = root;
      elements.rootPath.title = root;
    } catch (error) {
      state.options = cloneOptions(DEFAULT_OPTIONS);
      renderSchemaContract();
      renderDeviceModelOptions(state.draft.device_model);
      const currentRoot = elements.rootPath.textContent.trim();
      if (!currentRoot || currentRoot.includes("正在连接")) {
        elements.rootPath.textContent = "10-temp_label";
      }
      showToast("配置读取失败", `${error.message}；已使用内置枚举。`, "warning", 6500);
    } finally {
      // 统计只依赖当前目录配置，无需等待完整图片列表扫描结束。
      updateControls();
    }
  }

  async function checkHealth() {
    try {
      const health = await apiRequest("/health");
      const currentGeneration = parseDirectoryGeneration(health);
      if (
        state.directoryGeneration != null
        && currentGeneration != null
        && currentGeneration !== state.directoryGeneration
      ) {
        markDirectoryStale(health);
        return;
      }
      setServiceStatus("online", "服务正常");
    } catch (_error) {
      setServiceStatus("offline", "服务异常");
    }
  }

  function normaliseImageItems(payload) {
    const rawItems = Array.isArray(payload && payload.items) ? payload.items : [];
    return rawItems.map((item) => ({
      ...item,
      id: item.id != null ? item.id : item.image_id,
      annotated: item.annotated != null ? item.annotated : Boolean(item.annotation_exists)
    }));
  }

  function applyImageSummary(payload) {
    const summary = payload && payload.summary ? payload.summary : payload;
    state.counts = {
      total: numberOr(summary && summary.total, state.images.length),
      labeled: numberOr(summary && summary.labeled, countImages("labeled")),
      unlabeled: numberOr(summary && summary.unlabeled, countImages("unlabeled")),
      invalid: numberOr(summary && summary.invalid, countImages("invalid"))
    };
  }

  function mergeImageItems(incomingItems) {
    const previousItems = state.images;
    const previousById = new Map(
      previousItems.map((item) => [imageKey(item.id), item])
    );
    const selectedId = currentImageKey();
    let selectedItem = null;
    const merged = incomingItems.map((incoming) => {
      const id = imageKey(incoming.id);
      const previous = previousById.get(id);
      const item = previous ? { ...previous, ...incoming } : incoming;
      if (id === selectedId) selectedItem = item;
      return item;
    });

    // 拍摄端若正在替换文件，短暂的删除事件不能把标注人员当前图片抢走。
    // 当前项会在用户切换到别的图片后的下一次同步中自然移除。
    if (state.currentImage && !selectedItem) {
      const previousIndex = previousItems.findIndex(
        (item) => imageKey(item.id) === selectedId
      );
      const insertionIndex = previousIndex < 0
        ? merged.length
        : Math.min(previousIndex, merged.length);
      merged.splice(insertionIndex, 0, state.currentImage);
      selectedItem = state.currentImage;
    }

    state.images = merged;
    if (selectedItem) state.currentImage = selectedItem;

    const liveIds = new Set(merged.map((item) => imageKey(item.id)));
    for (const draftId of state.draftImageIds) {
      if (!liveIds.has(draftId) && draftId !== selectedId) {
        state.draftImageIds.delete(draftId);
      }
    }
  }

  function clearImageSyncTimer(name) {
    if (state[name] == null) return;
    window.clearTimeout(state[name]);
    state[name] = null;
  }

  function closeImageEventSource() {
    const source = state.imageEventSource;
    state.imageEventSource = null;
    state.imageEventGeneration = null;
    state.imageEventConnected = false;
    if (source) source.close();
  }

  function stopImageRealtimeSync() {
    clearImageSyncTimer("imageEventReconnectTimer");
    clearImageSyncTimer("imageSyncDebounceTimer");
    clearImageSyncTimer("imageCalibrationTimer");
    state.imageSyncPending = false;
    state.imageSyncAutoSelect = false;
    closeImageEventSource();
  }

  function imageSyncCanRun() {
    return !document.hidden
      && state.directoryGeneration != null
      && !state.switchingFolder
      && !state.directoryStale;
  }

  function scheduleImageCalibration(delay) {
    clearImageSyncTimer("imageCalibrationTimer");
    if (!imageSyncCanRun()) return;
    const fallbackDelay = state.imageEventConnected
      ? IMAGE_CALIBRATION_CONNECTED_MS
      : IMAGE_CALIBRATION_FALLBACK_MS;
    state.imageCalibrationTimer = window.setTimeout(() => {
      state.imageCalibrationTimer = null;
      scheduleSilentImageSync({ delay: 0, autoSelectFirst: true });
    }, delay == null ? fallbackDelay : delay);
  }

  function scheduleSilentImageSync(options) {
    const settings = options || {};
    if (!imageSyncCanRun()) return;
    state.imageSyncAutoSelect = state.imageSyncAutoSelect || Boolean(settings.autoSelectFirst);
    clearImageSyncTimer("imageCalibrationTimer");
    // Keep the first deadline so continuous camera events cannot starve syncing.
    if (state.imageSyncDebounceTimer != null && settings.delay !== 0) return;
    clearImageSyncTimer("imageSyncDebounceTimer");
    const delay = Number.isFinite(Number(settings.delay))
      ? Math.max(0, Number(settings.delay))
      : IMAGE_SYNC_DEBOUNCE_MS;
    state.imageSyncDebounceTimer = window.setTimeout(async () => {
      state.imageSyncDebounceTimer = null;
      const autoSelectFirst = state.imageSyncAutoSelect;
      state.imageSyncAutoSelect = false;
      await syncImagesSilently({ autoSelectFirst });
    }, delay);
  }

  async function syncImagesSilently(options) {
    const settings = options || {};
    if (!imageSyncCanRun()) return false;
    if (state.saving || state.deleting) {
      scheduleSilentImageSync({
        delay: 500,
        autoSelectFirst: settings.autoSelectFirst
      });
      return false;
    }
    if (state.imageSyncInFlight) {
      state.imageSyncPending = true;
      state.imageSyncAutoSelect = state.imageSyncAutoSelect || Boolean(settings.autoSelectFirst);
      return false;
    }

    const requestedGeneration = state.directoryGeneration;
    const listRevision = state.imageListRevision;
    state.imageSyncInFlight = true;
    try {
      const payload = await apiRequest(withDirectoryGeneration("/images"));
      if (
        document.hidden
        || requestedGeneration !== state.directoryGeneration
        || state.switchingFolder
        || state.directoryStale
        || state.deleting
        || state.saving
        || listRevision !== state.imageListRevision
      ) return false;

      const payloadGeneration = parseDirectoryGeneration(payload);
      if (payloadGeneration != null && payloadGeneration !== requestedGeneration) {
        markDirectoryStale(payload);
        return false;
      }

      const incoming = normaliseImageItems(payload);
      if (state.imageStateUncertain) {
        const remaining = incoming.find((item) => imageKey(item.id) === currentImageKey());
        if (!remaining) clearCurrentImage();
        else applyDeletedImageState(remaining.id, {
          image_exists: true, annotation_exists: remaining.annotation_exists
        });
        state.imageStateUncertain = false;
      }
      const previousImage = state.currentImage;
      mergeImageItems(incoming);
      applyImageSummary(payload);
      updateStats();
      applyListFilters();
      updateControls();
      setServiceStatus("online", "服务正常");
      if (previousImage && state.currentImage && (
        previousImage.modified_at !== state.currentImage.modified_at
        || previousImage.size !== state.currentImage.size
      )) loadPreview(state.currentImage);

      const canAutoSelect = Boolean(settings.autoSelectFirst)
        && !state.currentImage
        && !state.loadingAnnotation
        && !state.saving;
      if (canAutoSelect && state.filteredImages.length) {
        await selectImage(state.filteredImages[0].id, { skipPrompt: true });
      }
      return true;
    } catch (error) {
      if (requestedGeneration !== state.directoryGeneration) return false;
      if (isDirectoryChangedError(error)) {
        markDirectoryStale(error.payload && error.payload.detail);
      }
      return false;
    } finally {
      state.imageSyncInFlight = false;
      if (state.imageSyncPending && imageSyncCanRun()) {
        const autoSelectFirst = state.imageSyncAutoSelect;
        state.imageSyncPending = false;
        state.imageSyncAutoSelect = false;
        scheduleSilentImageSync({ autoSelectFirst });
      } else {
        state.imageSyncPending = false;
        scheduleImageCalibration();
      }
    }
  }

  function scheduleImageEventReconnect(generation) {
    clearImageSyncTimer("imageEventReconnectTimer");
    if (!imageSyncCanRun() || generation !== state.directoryGeneration) return;
    const exponent = Math.max(0, Math.min(state.imageEventFailures - 1, 5));
    const delay = Math.min(
      IMAGE_EVENT_RECONNECT_BASE_MS * (2 ** exponent),
      IMAGE_EVENT_RECONNECT_MAX_MS
    );
    state.imageEventReconnectTimer = window.setTimeout(() => {
      state.imageEventReconnectTimer = null;
      if (generation === state.directoryGeneration) connectImageEvents();
    }, delay);
  }

  function handleImageEvent(source, generation, event) {
    if (source !== state.imageEventSource || generation !== state.directoryGeneration) return;
    let payload;
    try {
      payload = event && event.data ? JSON.parse(event.data) : {};
    } catch (_error) {
      return;
    }

    const eventGeneration = parseDirectoryGeneration(payload);
    if (eventGeneration != null && eventGeneration !== generation) {
      markDirectoryStale(payload);
      return;
    }
    const type = String(payload && payload.type || "change").toLocaleLowerCase();
    if (type === "directory-changed") {
      markDirectoryStale(payload);
      return;
    }
    if (
      type === "ready"
      || type === "heartbeat"
      || type === "keepalive"
      || type === "ping"
    ) return;
    scheduleSilentImageSync({ autoSelectFirst: true });
  }

  function connectImageEvents() {
    if (!imageSyncCanRun()) return;
    const generation = state.directoryGeneration;
    if (
      state.imageEventSource
      && state.imageEventGeneration === generation
    ) return;

    closeImageEventSource();
    if (typeof window.EventSource !== "function") {
      state.imageEventFailures = Math.max(1, state.imageEventFailures);
      scheduleImageCalibration(IMAGE_CALIBRATION_FALLBACK_MS);
      return;
    }

    let source;
    try {
      source = new window.EventSource(
        `${API_ROOT}/image-events?directory_generation=${encodeURIComponent(generation)}`
      );
    } catch (_error) {
      state.imageEventFailures += 1;
      scheduleImageEventReconnect(generation);
      scheduleImageCalibration(IMAGE_CALIBRATION_FALLBACK_MS);
      return;
    }

    state.imageEventSource = source;
    state.imageEventGeneration = generation;
    const receive = (event) => handleImageEvent(source, generation, event);
    source.onopen = () => {
      if (source !== state.imageEventSource || generation !== state.directoryGeneration) return;
      state.imageEventConnected = true;
      state.imageEventFailures = 0;
      clearImageSyncTimer("imageEventReconnectTimer");
      // Reconcile the gap between the initial list and subscription/reconnection.
      scheduleSilentImageSync({ delay: 0, autoSelectFirst: true });
    };
    source.onmessage = receive;
    for (const eventName of [
      "ready",
      "image",
      "images",
      "change",
      "created",
      "modified",
      "deleted",
      "image-created",
      "image-modified",
      "image-moved",
      "image-deleted",
      "directory-changed",
      "resync"
    ]) {
      source.addEventListener(eventName, receive);
    }
    source.onerror = () => {
      if (source !== state.imageEventSource) return;
      closeImageEventSource();
      state.imageEventFailures += 1;
      scheduleImageEventReconnect(generation);
      scheduleImageCalibration(IMAGE_CALIBRATION_FALLBACK_MS);
    };
  }

  function startImageRealtimeSync() {
    if (!imageSyncCanRun()) return;
    connectImageEvents();
    scheduleImageCalibration();
  }

  async function loadImages(options) {
    const settings = options || {};
    showListLoading();
    try {
      const payload = await apiRequest(withDirectoryGeneration("/images"));
      state.images = normaliseImageItems(payload);
      applyImageSummary(payload);
      updateStats();
      applyListFilters();
      setServiceStatus("online", "服务正常");
      if (state.filteredImages.length && settings.selectFirst !== false) {
        await selectImage(state.filteredImages[0].id, { skipPrompt: true });
      }
      return true;
    } catch (error) {
      if (isDirectoryChangedError(error)) {
        markDirectoryStale(error.payload && error.payload.detail);
        return false;
      }
      state.images = [];
      state.filteredImages = [];
      renderImageList();
      setServiceStatus("offline", "无法连接");
      showToast("图像列表读取失败", error.message, "error", 8000);
      return false;
    }
  }

  async function downloadAnnotatedData() {
    if (state.downloading || state.switchingFolder || state.deleting || state.directoryStale || state.directoryGeneration == null) return;
    state.downloading = true;
    const generation = state.directoryGeneration;
    const folderName = String(state.config && state.config.data_dir || elements.rootPath.textContent)
      .replace(/\\/g, "/").split("/").filter(Boolean).pop() || "已标注数据";
    updateControls();
    try {
      const response = await fetch(`${API_ROOT}${withDirectoryGeneration("/export")}`, { cache: "no-store" });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const error = new Error(extractErrorMessage(payload, `下载失败（HTTP ${response.status}）`));
        error.payload = payload;
        error.code = payload && payload.detail && payload.detail.code;
        throw error;
      }
      const blob = await response.blob();
      if (generation !== state.directoryGeneration || state.directoryStale) return;
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      let filename = `${folderName}.zip`;
      if (match) {
        try { filename = decodeURIComponent(match[1]); } catch (_error) { /* Keep the folder name. */ }
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      showToast("下载已准备好", filename, "success");
    } catch (error) {
      if (generation !== state.directoryGeneration) return;
      if (isDirectoryChangedError(error)) markDirectoryStale(error.payload && error.payload.detail);
      showToast("下载未完成", error.message, error.code === "no_annotated_data" ? "info" : "error", 8000);
    } finally {
      state.downloading = false;
      updateControls();
    }
  }

  function clearCurrentImage() {
    state.loadSequence += 1;
    clearImageSyncTimer("previewRetryTimer");
    if (state.currentImage) state.draftImageIds.delete(currentImageKey());
    state.currentImage = null;
    state.annotationExists = false;
    state.annotationInvalid = false;
    state.revision = null;
    state.loadingAnnotation = false;
    state.imageStateUncertain = false;
    elements.currentFileName.textContent = "尚未选择图像";
    elements.currentFileName.title = "";
    elements.previewImage.hidden = true;
    elements.previewImage.dataset.imageId = "";
    elements.previewImage.removeAttribute("src");
    elements.viewerLoading.classList.add("hidden");
    elements.imageError.classList.add("hidden");
    elements.zoomToolbar.hidden = true;
    elements.emptyView.classList.remove("hidden");
    clearValidationErrors();
    setDraft(createEmptyAnnotations(state.config), false);
    showBanner("info", "等待选择", "选择图像后开始标注；新照片写入完成后会自动出现。");
    setAnnotationState("idle", "等待选择");
    setSaveState("", "选择图像后开始标注");
  }

  function applyDeletedImageState(imageId, outcome) {
    if (!outcome || typeof outcome.image_exists !== "boolean") return;
    state.imageStateUncertain = false;
    if (!outcome.image_exists) {
      if (currentImageKey() === imageKey(imageId)) clearCurrentImage();
      state.images = state.images.filter((item) => imageKey(item.id) !== imageKey(imageId));
      state.draftImageIds.delete(imageKey(imageId));
    } else if (typeof outcome.annotation_exists === "boolean") {
      const item = state.images.find((image) => imageKey(image.id) === imageKey(imageId));
      if (item) {
        item.annotated = outcome.annotation_exists;
        item.annotation_exists = outcome.annotation_exists;
        if (!outcome.annotation_exists) {
          item.annotation_valid = null;
          item.revision = "__missing__";
        }
      }
      if (currentImageKey() === imageKey(imageId)) {
        const hadAnnotation = state.annotationExists;
        state.annotationExists = outcome.annotation_exists;
        if (!outcome.annotation_exists) {
          state.annotationInvalid = false;
          state.revision = "__missing__";
          if (hadAnnotation || state.dirty) {
            // The retained form is now the only copy; protect it on navigation.
            markDirty();
          } else {
            setAnnotationState("unlabeled", "未标注");
            setSaveState("", "尚未保存标注");
          }
        }
      }
    }
    updateStats(true);
    applyListFilters();
  }

  async function deleteCurrentImage() {
    if (!state.currentImage || state.deleting || state.downloading || state.saving || state.loadingAnnotation || state.switchingFolder || state.directoryStale || state.imageStateUncertain) return;
    const imageId = currentImageKey();
    const imageName = String(state.currentImage.name || imageId);
    if (!window.confirm(`删除照片：${imageName}\n\n将从磁盘永久删除原图及对应标注文件，无法恢复${state.dirty ? "\n当前未保存的标注也会丢失。" : ""}\n\n确认删除吗？`)) return;

    const generation = state.directoryGeneration;
    const index = currentFilteredIndex();
    const nextIds = state.filteredImages.slice(index + 1).map((item) => imageKey(item.id));
    state.deleting = true;
    state.imageListRevision += 1;
    updateControls();
    let deletionError = null;
    let outcome = null;
    try {
      try {
        outcome = await apiRequest(withDirectoryGeneration(`/image?image_id=${encodeURIComponent(imageId)}`), {
          method: "DELETE", headers: { "X-Requested-With": "annotation-ui" }
        });
      } catch (error) {
        deletionError = error;
        outcome = error.payload && error.payload.detail;
        if (isDirectoryChangedError(error)) {
          markDirectoryStale(outcome);
          return;
        }
      }
      if (generation !== state.directoryGeneration || state.directoryStale) return;
      applyDeletedImageState(imageId, outcome);
      try {
        const payload = await apiRequest(withDirectoryGeneration("/images"));
        if (generation !== state.directoryGeneration || state.directoryStale) return;
        const incoming = normaliseImageItems(payload);
        const remaining = incoming.find((item) => imageKey(item.id) === imageId);
        if (!remaining && outcome?.image_exists !== true && currentImageKey() === imageId) clearCurrentImage();
        mergeImageItems(incoming);
        if (remaining) applyDeletedImageState(imageId, {
          image_exists: true, annotation_exists: remaining.annotation_exists
        });
        state.imageStateUncertain = false;
        applyImageSummary(payload);
      } catch (error) {
        if (isDirectoryChangedError(error)) {
          markDirectoryStale(error.payload && error.payload.detail);
          return;
        }
        if (typeof outcome?.image_exists !== "boolean") state.imageStateUncertain = true;
        showToast("列表同步失败", `${error.message}；恢复连接后将自动核对磁盘状态。`, "warning", 8500);
      }
      if (deletionError) {
        showToast("删除失败", deletionError.message, "error", 10000);
        if (state.currentImage) showBanner("error", "删除失败", deletionError.message);
      } else {
        showToast("已永久删除", imageName, "success");
      }
    } finally {
      state.deleting = false;
      state.imageListRevision += 1;
      updateStats();
      applyListFilters();
      updateControls();
      if (generation === state.directoryGeneration && !state.directoryStale) {
        if (!state.currentImage && state.filteredImages.length) {
          const nextId = nextIds.find((id) => state.filteredImages.some((item) => imageKey(item.id) === id));
          const fallback = state.filteredImages[Math.max(0, Math.min(index, state.filteredImages.length - 1))];
          await selectImage(nextId || fallback.id, { skipPrompt: true });
        }
        if (elements.statisticsDialog.open) loadStatistics();
        scheduleSilentImageSync({ delay: 0, autoSelectFirst: true });
      }
    }
  }

  function setFolderSwitchBusy(busy) {
    state.switchingFolder = Boolean(busy);
    elements.chooseDataDirButton.setAttribute("aria-busy", busy ? "true" : "false");
    elements.chooseDataDirButtonText.textContent = busy ? "切换中…" : "切换文件夹";
    elements.folderSwitchStatus.textContent = busy ? "正在切换数据文件夹" : "";
    elements.cancelDataDirectoryButton.disabled = Boolean(busy);
    elements.cancelDataDirectoryIcon.disabled = Boolean(busy);
    elements.confirmDataDirectoryButton.disabled = Boolean(busy);
    elements.confirmDataDirectoryButtonText.textContent = busy ? "正在切换…" : "选择此文件夹";
    updateDirectoryBrowserControls();
    updateControls();
  }

  function setDirectoryBrowserBusy(busy) {
    state.browsingFolder = Boolean(busy);
    elements.dataDirectoryBrowser.setAttribute("aria-busy", busy ? "true" : "false");
    elements.dataDirectoryLoading.hidden = !busy;
    if (busy) {
      elements.dataDirectoryEmpty.hidden = true;
      elements.dataDirectoryList.hidden = true;
    }
    updateDirectoryBrowserControls();
  }

  function updateDirectoryBrowserControls() {
    const disabled = state.switchingFolder || state.browsingFolder;
    elements.dataDirectoryPathInput.disabled = disabled;
    elements.cancelDataDirectoryButton.disabled = disabled;
    elements.cancelDataDirectoryIcon.disabled = disabled;
    elements.dataDirectoryGoButton.disabled = disabled;
    elements.dataDirectoryRefreshButton.disabled = disabled;
    elements.dataDirectoryUpButton.disabled = disabled || (
      state.directoryBrowseMode === "roots" && !state.directoryBrowseParentPath
    );
    if (!state.switchingFolder) {
      elements.confirmDataDirectoryButton.disabled = state.browsingFolder || !state.directorySelectedPath;
    }
    for (const item of elements.dataDirectoryList.querySelectorAll(".directory-browser-item")) {
      item.disabled = disabled;
    }
    for (const item of elements.dataDirectoryBreadcrumbs.querySelectorAll(".directory-breadcrumb")) {
      item.disabled = disabled || item.getAttribute("aria-current") === "location";
    }
  }

  function normaliseDirectoryItem(item) {
    if (typeof item === "string") {
      const path = item.trim();
      if (!path) return null;
      return { name: path === "/" ? "/" : (path.split("/").filter(Boolean).pop() || path), path };
    }
    if (!item || typeof item !== "object") return null;
    const path = String(item.path || item.full_path || item.data_dir || "").trim();
    if (!path) return null;
    const name = String(item.name || item.label || "").trim()
      || (path === "/" ? "/" : path.split("/").filter(Boolean).pop())
      || path;
    return { name, path };
  }

  function selectDataDirectory(path, button) {
    const selectedPath = String(path || "").trim();
    if (!selectedPath) return;
    state.directorySelectedPath = selectedPath;
    elements.selectedDataDirectoryText.textContent = selectedPath;
    elements.dataDirectoryPathInput.value = selectedPath;
    for (const item of elements.dataDirectoryList.querySelectorAll(".directory-browser-item")) {
      const selected = item.dataset.path === selectedPath;
      item.classList.toggle("directory-browser-item--selected", selected);
      item.setAttribute("aria-selected", selected ? "true" : "false");
    }
    if (button) button.focus({ preventScroll: true });
    elements.dataDirectoryError.textContent = "";
    updateDirectoryBrowserControls();
  }

  function renderDirectoryBreadcrumbs(items, mode) {
    const fragment = document.createDocumentFragment();
    const rootButton = document.createElement("button");
    rootButton.type = "button";
    rootButton.className = "directory-breadcrumb";
    rootButton.textContent = "可用位置";
    rootButton.dataset.path = "";
    rootButton.disabled = mode === "roots";
    rootButton.addEventListener("click", () => browseDataDirectories(null));
    fragment.append(rootButton);

    for (const rawItem of items || []) {
      const item = normaliseDirectoryItem(rawItem);
      if (!item) continue;
      const separator = document.createElement("span");
      separator.className = "directory-breadcrumb-separator";
      separator.textContent = ">";
      separator.setAttribute("aria-hidden", "true");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "directory-breadcrumb";
      button.textContent = item.name;
      button.title = item.path;
      button.dataset.path = item.path;
      button.addEventListener("click", () => browseDataDirectories(item.path));
      fragment.append(separator, button);
    }
    const lastButton = fragment.lastElementChild;
    if (lastButton && lastButton.matches("button")) {
      lastButton.disabled = mode !== "roots";
      lastButton.setAttribute("aria-current", "location");
    }
    elements.dataDirectoryBreadcrumbs.replaceChildren(fragment);
    elements.dataDirectoryBreadcrumbs.scrollLeft = elements.dataDirectoryBreadcrumbs.scrollWidth;
  }

  function renderDirectoryList(items) {
    const directories = (items || []).map(normaliseDirectoryItem).filter(Boolean);
    const fragment = document.createDocumentFragment();
    for (const item of directories) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "directory-browser-item";
      button.dataset.path = item.path;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", "false");
      button.title = `${item.path}\n单击选择，双击进入`;
      const icon = document.createElement("span");
      icon.className = "directory-browser-item-icon";
      icon.setAttribute("aria-hidden", "true");
      const copy = document.createElement("span");
      copy.className = "directory-browser-item-copy";
      const name = document.createElement("strong");
      name.textContent = item.name;
      const path = document.createElement("small");
      path.textContent = item.path;
      copy.append(name, path);
      button.append(icon, copy);
      button.addEventListener("click", () => selectDataDirectory(item.path, button));
      button.addEventListener("dblclick", () => browseDataDirectories(item.path));
      button.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        browseDataDirectories(item.path);
      });
      fragment.append(button);
    }
    elements.dataDirectoryList.replaceChildren(fragment);
    elements.dataDirectoryList.hidden = directories.length === 0;
    elements.dataDirectoryEmpty.hidden = directories.length !== 0;
  }

  async function browseDataDirectories(path) {
    if (state.switchingFolder || state.browsingFolder) return;
    const requestedPath = path == null ? "" : String(path).trim();
    if (requestedPath && !requestedPath.startsWith("/")) {
      elements.dataDirectoryError.textContent = "请输入以 / 开头的 Linux 服务器绝对路径。";
      return;
    }
    const sequence = ++state.directoryBrowseSequence;
    elements.dataDirectoryError.textContent = "";
    setDirectoryBrowserBusy(true);
    try {
      const params = new URLSearchParams();
      if (requestedPath) params.set("path", requestedPath);
      if (state.directorySwitchGeneration != null) {
        params.set("directory_generation", String(state.directorySwitchGeneration));
      }
      const result = await apiRequest(`/data-directories?${params.toString()}`, {
        headers: { "X-Requested-With": "annotation-ui" }
      });
      if (sequence !== state.directoryBrowseSequence || !elements.dataDirectoryDialog.open) return;
      const generation = parseDirectoryGeneration(result);
      if (generation != null) state.directorySwitchGeneration = generation;
      const mode = result && result.mode === "roots" ? "roots" : "directory";
      const currentPath = String(result && (result.current_path || result.browse_path || result.path) || "").trim();
      const parentPathValue = result && (result.parent_path ?? result.parent);
      const parentPath = parentPathValue == null ? null : String(parentPathValue);
      const directories = Array.isArray(result && result.directories)
        ? result.directories
        : (Array.isArray(result && result.items) ? result.items : (Array.isArray(result && result.roots) ? result.roots : []));
      state.directoryBrowseMode = mode;
      state.directoryBrowsePath = currentPath;
      state.directoryBrowseParentPath = parentPath;
      state.directorySelectedPath = mode === "directory" ? currentPath : "";
      elements.dataDirectoryPathInput.value = currentPath;
      elements.browseDataDirectoryText.textContent = currentPath || "可用位置";
      elements.selectedDataDirectoryText.textContent = state.directorySelectedPath || "尚未选择";
      const allowedRoots = Array.isArray(result && result.allowed_data_roots)
        ? result.allowed_data_roots.map((item) => String(item)).filter(Boolean)
        : [];
      if (allowedRoots.length) elements.allowedDataRootsText.textContent = allowedRoots.join("；");
      renderDirectoryBreadcrumbs(result && result.breadcrumbs, mode);
      renderDirectoryList(directories);
      updateDirectoryBrowserControls();
    } catch (error) {
      if (sequence !== state.directoryBrowseSequence) return;
      if (isDirectoryChangedError(error)) {
        elements.dataDirectoryDialog.close();
        document.body.classList.remove("dialog-open");
        state.directorySwitchGeneration = null;
        markDirectoryStale(error.payload && error.payload.detail);
      } else {
        elements.dataDirectoryError.textContent = error.message;
        elements.dataDirectoryList.replaceChildren();
        elements.dataDirectoryList.hidden = true;
        elements.dataDirectoryEmpty.hidden = true;
      }
    } finally {
      if (sequence === state.directoryBrowseSequence) setDirectoryBrowserBusy(false);
    }
  }

  function browseEnteredDataDirectory() {
    const requestedPath = elements.dataDirectoryPathInput.value.trim();
    if (!requestedPath || !requestedPath.startsWith("/")) {
      elements.dataDirectoryError.textContent = "请输入以 / 开头的 Linux 服务器绝对路径。";
      elements.dataDirectoryPathInput.focus();
      return;
    }
    browseDataDirectories(requestedPath);
  }

  function resetWorkspaceForDirectorySwitch(dataDir, directoryGeneration) {
    stopImageRealtimeSync();
    state.imageEventFailures = 0;
    state.loadSequence += 1;
    state.imageListRevision += 1;
    state.imageStateUncertain = false;
    clearImageSyncTimer("previewRetryTimer");
    state.images = [];
    state.filteredImages = [];
    state.counts = { total: 0, labeled: 0, unlabeled: 0, invalid: 0 };
    state.currentImage = null;
    state.annotationExists = false;
    state.annotationInvalid = false;
    state.revision = null;
    state.dirty = false;
    state.editRevision = 0;
    state.loadingAnnotation = false;
    state.saving = false;
    state.statisticsLoading = false;
    state.statisticsSequence += 1;
    state.directoryGeneration = directoryGeneration;
    state.directoryStale = false;
    state.zoomFactor = 1;
    state.fitScale = 1;
    state.draftImageIds.clear();
    state.config = null;

    elements.search.value = "";
    elements.filter.value = "all";
    elements.rootPath.textContent = dataDir;
    elements.rootPath.title = dataDir;
    elements.currentFileName.textContent = "尚未选择图像";
    elements.currentFileName.title = "";
    elements.positionText.textContent = "0 / 0";
    elements.previewImage.hidden = true;
    elements.previewImage.dataset.imageId = "";
    elements.previewImage.removeAttribute("src");
    elements.viewerLoading.classList.add("hidden");
    elements.imageError.classList.add("hidden");
    elements.zoomToolbar.hidden = true;
    elements.emptyView.classList.remove("hidden");
    elements.imageList.replaceChildren();
    if (elements.statisticsDialog.open) elements.statisticsDialog.close();
    elements.statisticsContent.setAttribute("aria-busy", "false");
    elements.refreshStatisticsButton.setAttribute("aria-busy", "false");
    elements.refreshStatisticsButtonText.textContent = "刷新";

    showBanner("info", "等待选择", "选择图像后开始标注。");
    clearValidationErrors();
    setAnnotationState("idle", "等待选择");
    setSaveState("", "选择图像后开始标注");
    setDraft(createEmptyAnnotations(state.config), false);
    updateStats();
  }

  async function openDataDirectoryDialog() {
    if (state.switchingFolder || state.browsingFolder || state.loadingAnnotation || state.saving || state.deleting || state.downloading) return;

    let dialogConfig = state.config || {};
    if (state.directoryStale || state.directoryGeneration == null || !state.config) {
      try {
        dialogConfig = await apiRequest("/config");
      } catch (error) {
        showToast("无法读取当前目录", error.message, "error", 7000);
        return;
      }
    }
    const dialogGeneration = parseDirectoryGeneration(dialogConfig);
    if (dialogGeneration == null) {
      showToast("暂时无法切换", "服务未返回有效的目录状态，请刷新页面后重试。", "warning", 6500);
      return;
    }
    state.directorySwitchGeneration = dialogGeneration;
    state.directoryBrowseSequence += 1;
    state.directoryBrowseMode = "roots";
    state.directoryBrowsePath = "";
    state.directoryBrowseParentPath = null;
    state.directorySelectedPath = "";
    const currentPath = String(
      dialogConfig.data_dir || elements.rootPath.textContent || ""
    ).trim();
    const allowedRoots = Array.isArray(dialogConfig.allowed_data_roots)
      ? dialogConfig.allowed_data_roots.map((item) => String(item)).filter(Boolean)
      : [];
    elements.dataDirectoryPathInput.value = currentPath;
    elements.browseDataDirectoryText.textContent = "正在读取…";
    elements.selectedDataDirectoryText.textContent = "尚未选择";
    elements.currentDataDirectoryText.textContent = currentPath;
    elements.allowedDataRootsText.textContent = allowedRoots.length
      ? allowedRoots.join("；")
      : "未限制（建议在服务配置中设置）";
    elements.dataDirectoryError.textContent = "";
    elements.dataDirectoryList.replaceChildren();
    elements.dataDirectoryList.hidden = true;
    elements.dataDirectoryEmpty.hidden = true;
    elements.dataDirectoryDialog.showModal();
    document.body.classList.add("dialog-open");
    browseDataDirectories(currentPath);
  }

  function closeDataDirectoryDialog() {
    if (state.switchingFolder || state.browsingFolder || !elements.dataDirectoryDialog.open) return;
    state.directoryBrowseSequence += 1;
    state.browsingFolder = false;
    elements.dataDirectoryDialog.close();
    document.body.classList.remove("dialog-open");
    state.directorySwitchGeneration = null;
    state.directorySelectedPath = "";
    elements.chooseDataDirButton.focus({ preventScroll: true });
  }

  async function chooseDataDirectory(event) {
    event.preventDefault();
    if (state.switchingFolder || state.browsingFolder || state.loadingAnnotation || state.saving || state.deleting || state.downloading) return;
    if (state.directorySwitchGeneration == null) {
      elements.dataDirectoryError.textContent = "服务目录状态尚未就绪，请刷新页面后重试。";
      return;
    }
    const requestedPath = state.directorySelectedPath.trim();
    elements.dataDirectoryError.textContent = "";
    if (!requestedPath) {
      elements.dataDirectoryError.textContent = "请先在列表中选择一个文件夹；也可以输入地址并点击“转到”。";
      elements.dataDirectoryPathInput.focus();
      return;
    }
    if (!requestedPath.startsWith("/")) {
      elements.dataDirectoryError.textContent = "目录必须是以 / 开头的 Linux 绝对路径。";
      elements.dataDirectoryPathInput.focus();
      return;
    }
    if (state.dirty) {
      const leave = window.confirm("当前标注尚未保存。切换文件夹会放弃这些修改，是否继续？");
      if (!leave) return;
    }

    let busyReleased = false;
    const wasDirectoryStale = state.directoryStale;
    setFolderSwitchBusy(true);
    try {
      const result = await apiRequest("/data-directory/select", {
        method: "POST",
        headers: { "X-Requested-With": "annotation-ui" },
        body: JSON.stringify({
          path: requestedPath,
          directory_generation: state.directorySwitchGeneration
        })
      });
      const selectedGeneration = parseDirectoryGeneration(result);

      const dataDir = String(result.data_dir || "").trim();
      const generationChanged = selectedGeneration !== state.directoryGeneration;
      if (!result.changed && !generationChanged && !wasDirectoryStale) {
        if (dataDir) {
          elements.rootPath.textContent = dataDir;
          elements.rootPath.title = dataDir;
        }
        elements.dataDirectoryDialog.close();
        document.body.classList.remove("dialog-open");
        state.directorySwitchGeneration = null;
        showToast("文件夹未变化", "选择的是当前文件夹。", "info");
        return;
      }

      elements.dataDirectoryDialog.close();
      document.body.classList.remove("dialog-open");
      state.directorySwitchGeneration = null;
      resetWorkspaceForDirectorySwitch(dataDir, selectedGeneration);
      await loadConfig();
      renderAllChoiceGroups();
      const loaded = await loadImages({ selectFirst: false });
      setFolderSwitchBusy(false);
      busyReleased = true;
      if (loaded) {
        showToast(
          "文件夹已切换",
          elements.rootPath.textContent.trim() || dataDir || "图像列表已刷新",
          "success",
          5000
        );
      }

      startImageRealtimeSync();

      if (loaded && state.filteredImages.length) {
        await selectImage(state.filteredImages[0].id, { skipPrompt: true });
      }
    } catch (error) {
      if (isDirectoryChangedError(error)) {
        elements.dataDirectoryDialog.close();
        document.body.classList.remove("dialog-open");
        state.directorySwitchGeneration = null;
        markDirectoryStale(error.payload && error.payload.detail);
      } else {
        elements.dataDirectoryError.textContent = error.message;
        elements.dataDirectoryPathInput.focus();
      }
    } finally {
      if (!busyReleased) {
        setFolderSwitchBusy(false);
        startImageRealtimeSync();
        scheduleSilentImageSync({ delay: 0, autoSelectFirst: true });
      }
      if (elements.dataDirectoryDialog.open) {
        elements.dataDirectoryPathInput.focus({ preventScroll: true });
      } else {
        elements.chooseDataDirButton.focus({ preventScroll: true });
      }
    }
  }

  function numberOr(value, fallback) {
    return Number.isFinite(Number(value)) ? Number(value) : fallback;
  }

  function imageStatus(item) {
    if (state.draftImageIds.has(imageKey(item.id))) return "draft";
    if (item.annotated && item.annotation_valid === false) return "invalid";
    if (item.annotated) return "labeled";
    return "unlabeled";
  }

  function countImages(status) {
    return state.images.filter((item) => {
      const itemStatus = imageStatus(item);
      if (status === "labeled") return Boolean(item.annotated);
      if (status === "unlabeled") return !item.annotated;
      if (status === "invalid") return Boolean(item.annotated && item.annotation_valid === false);
      return itemStatus === status;
    }).length;
  }

  function updateStats(recalculate) {
    if (recalculate) {
      state.counts = {
        total: state.images.length,
        labeled: countImages("labeled"),
        unlabeled: countImages("unlabeled"),
        invalid: countImages("invalid")
      };
    }
    elements.totalCount.textContent = String(state.counts.total);
    elements.labeledCount.textContent = String(state.counts.labeled);
    elements.unlabeledCount.textContent = String(state.counts.unlabeled);
    elements.invalidCount.textContent = String(state.counts.invalid);
    elements.invalidStat.hidden = state.counts.invalid === 0;
  }

  function formatPercentage(value) {
    const percentage = Number.isFinite(Number(value)) ? Number(value) : 0;
    return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(percentage)}%`;
  }

  function setStatisticsBusy(busy) {
    state.statisticsLoading = Boolean(busy);
    elements.statisticsContent.setAttribute("aria-busy", busy ? "true" : "false");
    elements.refreshStatisticsButton.setAttribute("aria-busy", busy ? "true" : "false");
    elements.refreshStatisticsButtonText.textContent = busy ? "汇总中…" : "刷新";
    updateControls();
  }

  function renderStatisticsLoading() {
    elements.statisticsOverview.hidden = true;
    elements.statisticsContext.hidden = true;
    const loading = document.createElement("div");
    loading.className = "statistics-loading";
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    spinner.setAttribute("aria-hidden", "true");
    const title = document.createElement("strong");
    title.textContent = "正在汇总属性分布…";
    const text = document.createElement("p");
    text.textContent = "数据量较大时可能需要一点时间。";
    loading.append(spinner, title, text);
    elements.statisticsContent.replaceChildren(loading);
    elements.statisticsStatus.textContent = "正在汇总属性分布。";
  }

  function renderStatisticsMessage(type, titleText, message, retryable) {
    const messageBox = document.createElement("div");
    messageBox.className = `statistics-message statistics-message--${type}`;
    const mark = document.createElement("span");
    mark.className = "statistics-message-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = type === "error" ? "!" : "—";
    const title = document.createElement("strong");
    title.textContent = titleText;
    const text = document.createElement("p");
    text.textContent = message;
    messageBox.append(mark, title, text);
    if (retryable) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "button button--secondary button--compact";
      retry.textContent = "重新加载";
      retry.addEventListener("click", loadStatistics);
      messageBox.append(retry);
    }
    elements.statisticsContent.replaceChildren(messageBox);
    elements.statisticsStatus.textContent = `${titleText}。${message}`;
  }

  function renderStatisticsSummary(summary) {
    const total = numberOr(summary && summary.total, 0);
    const labeled = numberOr(summary && summary.labeled, 0);
    const valid = numberOr(summary && summary.valid, 0);
    const unlabeled = numberOr(summary && summary.unlabeled, 0);
    const invalid = numberOr(summary && summary.invalid, 0);
    const coverage = total > 0 ? valid * 100 / total : 0;

    elements.statisticsValidCount.textContent = String(valid);
    elements.statisticsLabeledDetail.textContent = `共 ${labeled} 份已保存 JSON`;
    elements.statisticsCoverage.textContent = formatPercentage(coverage);
    elements.statisticsTotalDetail.textContent = `共 ${total} 张图像`;
    elements.statisticsUnlabeledCount.textContent = String(unlabeled);
    elements.statisticsInvalidCount.textContent = String(invalid);
    elements.statisticsOverview.hidden = false;
    return { total, labeled, valid, unlabeled, invalid };
  }

  function createDistributionRow(item) {
    const count = numberOr(item && item.count, 0);
    const percentage = Math.max(0, Math.min(100, numberOr(item && item.percentage, 0)));
    const row = document.createElement("div");
    row.className = "distribution-row";
    row.setAttribute("role", "listitem");
    if (count === 0) row.classList.add("distribution-row--zero");
    if (item && item.is_missing) row.classList.add("distribution-row--missing");

    const meta = document.createElement("div");
    meta.className = "distribution-row-meta";
    const label = document.createElement("span");
    label.className = "distribution-value";
    label.textContent = String(item && item.label != null ? item.label : "未命名");
    label.title = label.textContent;
    const measure = document.createElement("span");
    measure.className = "distribution-measure";
    const amount = document.createElement("b");
    amount.textContent = `${count} 张`;
    const share = document.createElement("small");
    share.textContent = formatPercentage(percentage);
    measure.append(amount, share);
    meta.append(label, measure);

    const track = document.createElement("div");
    track.className = "distribution-track";
    track.setAttribute("aria-hidden", "true");
    const fill = document.createElement("span");
    fill.className = "distribution-fill";
    if (count > 0) fill.classList.add("distribution-fill--present");
    fill.style.width = `${percentage}%`;
    track.append(fill);
    row.append(meta, track);
    return row;
  }

  function createStatisticsFieldCard(field) {
    const card = document.createElement("article");
    card.className = "statistics-field-card";
    const header = document.createElement("header");
    header.className = "statistics-field-header";
    const heading = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = String(field && field.label ? field.label : field.name);
    const code = document.createElement("code");
    code.textContent = String(field && field.name ? field.name : "");
    heading.append(title, code);
    if (field && field.multiple) {
      const badge = document.createElement("span");
      badge.className = "statistics-field-badge";
      badge.textContent = "多选";
      header.append(heading, badge);
    } else {
      header.append(heading);
    }

    const valid = numberOr(field && field.valid_annotations, 0);
    const filled = numberOr(field && field.filled, 0);
    const missing = numberOr(field && field.missing, 0);
    const observed = numberOr(field && field.observed_values, 0);
    const meta = document.createElement("p");
    meta.className = "statistics-field-meta";
    meta.textContent = field && field.nullable
      ? `非空 ${filled} / ${valid} · 已出现 ${observed} 种值`
      : `统计基数 ${valid} 张 · 已出现 ${observed} 种值`;
    if (field && field.nullable && missing > 0) {
      const warning = document.createElement("span");
      warning.textContent = ` · ${missing} 张未填写`;
      meta.append(warning);
    }

    const sourceValues = Array.isArray(field && field.values) ? Array.from(field.values) : [];
    sourceValues.sort((left, right) => {
      const countDifference = numberOr(right && right.count, 0) - numberOr(left && left.count, 0);
      if (countDifference) return countDifference;
      return String(left && left.label || "").localeCompare(String(right && right.label || ""), "zh-CN", { numeric: true });
    });

    const initialLimit = 12;
    const list = document.createElement("div");
    list.className = "distribution-list";
    list.id = `statisticsValues-${String(field && field.name || "field")}`;
    list.setAttribute("role", "list");
    list.setAttribute(
      "aria-label",
      `${title.textContent}值分布${sourceValues.length > initialLimit ? "，展开后可滚动" : ""}`
    );
    if (sourceValues.length > initialLimit) list.tabIndex = 0;
    let expanded = false;
    const drawRows = () => {
      const visibleValues = expanded ? sourceValues : sourceValues.slice(0, initialLimit);
      list.replaceChildren(...visibleValues.map(createDistributionRow));
    };
    drawRows();

    card.append(header, meta);
    if (sourceValues.length) {
      card.append(list);
    } else {
      const empty = document.createElement("p");
      empty.className = "distribution-empty";
      empty.textContent = "暂无可展示的值";
      card.append(empty);
    }

    if (sourceValues.length > initialLimit) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "distribution-toggle";
      const updateToggle = () => {
        toggle.textContent = expanded
          ? "收起"
          : `展开全部 ${sourceValues.length} 个值`;
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
      };
      updateToggle();
      toggle.setAttribute("aria-controls", list.id);
      toggle.addEventListener("click", () => {
        expanded = !expanded;
        drawRows();
        updateToggle();
      });
      card.append(toggle);
    }
    return card;
  }

  function renderStatistics(payload) {
    const summary = renderStatisticsSummary(payload && payload.summary);
    const fields = Array.isArray(payload && payload.fields) ? payload.fields : [];
    if (!summary.valid) {
      elements.statisticsContext.hidden = true;
      renderStatisticsMessage(
        "empty",
        "还没有可统计的有效标注",
        summary.invalid
          ? "请先修复异常 JSON，或继续完成并保存标注。"
          : "完成并保存标注后，这里会展示八项属性的值分布。",
        false
      );
      return;
    }
    if (!fields.length) {
      elements.statisticsContext.hidden = true;
      renderStatisticsMessage("empty", "暂无属性数据", "当前有效标注中没有可展示的属性。", false);
      return;
    }

    const grid = document.createElement("div");
    grid.className = "statistics-field-grid";
    grid.append(...fields.map(createStatisticsFieldCard));
    elements.statisticsContent.replaceChildren(grid);
    elements.statisticsContext.hidden = false;
    elements.statisticsStatus.textContent = `统计加载完成，共 ${summary.valid} 份有效标注，${fields.length} 项属性。`;
    elements.statisticsUpdatedAt.textContent = `更新于 ${new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    }).format(new Date())}`;
  }

  async function loadStatistics() {
    if (state.statisticsLoading || state.directoryStale || state.directoryGeneration == null) return;
    const sequence = ++state.statisticsSequence;
    const requestedGeneration = state.directoryGeneration;
    setStatisticsBusy(true);
    renderStatisticsLoading();
    try {
      const payload = await apiRequest(withDirectoryGeneration("/statistics"));
      const responseGeneration = parseDirectoryGeneration(payload);
      if (
        sequence !== state.statisticsSequence
        || requestedGeneration !== state.directoryGeneration
      ) return;
      if (
        responseGeneration != null
        && requestedGeneration != null
        && responseGeneration !== requestedGeneration
      ) {
        markDirectoryStale(payload);
        return;
      }
      renderStatistics(payload || {});
      setServiceStatus("online", "服务正常");
    } catch (error) {
      if (
        sequence !== state.statisticsSequence
        || requestedGeneration !== state.directoryGeneration
      ) return;
      if (isDirectoryChangedError(error)) {
        markDirectoryStale(error.payload && error.payload.detail);
      }
      elements.statisticsOverview.hidden = true;
      elements.statisticsContext.hidden = true;
      renderStatisticsMessage("error", "统计加载失败", error.message, !state.directoryStale);
    } finally {
      if (sequence === state.statisticsSequence) setStatisticsBusy(false);
    }
  }

  function openStatistics() {
    if (state.switchingFolder || state.directoryStale || state.directoryGeneration == null) return;
    if (!elements.statisticsDialog.open) elements.statisticsDialog.showModal();
    document.body.classList.add("dialog-open");
    loadStatistics();
  }

  function closeStatistics() {
    if (elements.statisticsDialog.open) elements.statisticsDialog.close();
  }

  function showListLoading() {
    elements.imageList.replaceChildren(elements.listPlaceholder);
    elements.listPlaceholder.classList.remove("hidden");
  }

  function applyListFilters() {
    const query = elements.search.value.trim().toLocaleLowerCase("zh-CN");
    const filter = elements.filter.value;
    state.filteredImages = state.images.filter((item) => {
      const name = String(item.name || item.id || "").toLocaleLowerCase("zh-CN");
      if (query && !name.includes(query)) return false;
      const status = imageStatus(item);
      if (filter === "all") return true;
      if (filter === "unlabeled") return status === "unlabeled" || status === "draft";
      if (filter === "labeled") return Boolean(item.annotated);
      return status === filter;
    });
    elements.visibleCount.textContent = `${state.filteredImages.length} 张`;
    renderImageList();
    updateNavigation();
  }

  function renderImageList() {
    const scrollTop = elements.imageList.scrollTop;
    const focusedId = document.activeElement && document.activeElement.dataset.imageId;
    const existing = new Map(Array.from(elements.imageList.querySelectorAll(".image-item"))
      .map((item) => [item.dataset.imageId, item]));
    const fragment = document.createDocumentFragment();
    if (!state.filteredImages.length) {
      const empty = document.createElement("div");
      empty.className = "empty-list";
      const title = document.createElement("strong");
      title.textContent = state.images.length ? "没有匹配的图像" : "目录中没有图像";
      const text = document.createElement("p");
      text.textContent = state.images.length ? "请更换关键词或标注状态筛选。" : "当前文件夹中没有支持的图像。";
      empty.append(title, text);
      fragment.append(empty);
    } else {
      for (const item of state.filteredImages) {
        const previous = existing.get(imageKey(item.id));
        if (previous && previous.dataset.status === imageStatus(item) && previous.title === String(item.name || item.id)) {
          previous.setAttribute("aria-selected", imageKey(item.id) === currentImageKey() ? "true" : "false");
          fragment.append(previous);
        } else {
          fragment.append(createImageListItem(item));
        }
      }
    }
    elements.imageList.replaceChildren(fragment);
    if (focusedId) {
      const focused = Array.from(elements.imageList.querySelectorAll(".image-item"))
        .find((item) => item.dataset.imageId === focusedId);
      if (focused) focused.focus({ preventScroll: true });
    }
    elements.imageList.scrollTop = scrollTop;
  }

  function createImageListItem(item) {
    const status = imageStatus(item);
    const labels = {
      labeled: "已标注",
      unlabeled: "未标注",
      invalid: "JSON 异常",
      draft: "待审核草稿"
    };
    const button = document.createElement("button");
    button.type = "button";
    button.className = "image-item";
    button.dataset.imageId = imageKey(item.id);
    button.dataset.status = status;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", imageKey(item.id) === currentImageKey() ? "true" : "false");
    button.title = String(item.name || item.id);
    button.disabled = state.saving || state.deleting || state.switchingFolder || state.directoryStale;

    const thumb = document.createElement("span");
    thumb.className = "image-thumb";
    const extension = String(item.name || "").split(".").pop();
    thumb.textContent = extension ? extension.slice(0, 4).toUpperCase() : "IMG";

    const copy = document.createElement("span");
    copy.className = "image-item-copy";
    const name = document.createElement("span");
    name.className = "image-item-name";
    name.textContent = String(item.name || item.id);
    const itemState = document.createElement("span");
    itemState.className = `image-item-state image-item-state--${status}`;
    itemState.textContent = labels[status];
    copy.append(name, itemState);

    const dot = document.createElement("span");
    dot.className = `state-dot state-dot--${status}`;
    dot.title = labels[status];
    dot.setAttribute("aria-label", labels[status]);
    button.append(thumb, copy, dot);
    button.addEventListener("click", () => selectImage(item.id));
    return button;
  }

  function updateSelectedListItem() {
    for (const item of elements.imageList.querySelectorAll(".image-item")) {
      item.setAttribute("aria-selected", item.dataset.imageId === currentImageKey() ? "true" : "false");
    }
  }

  function canDiscardCurrentChanges() {
    if (!state.dirty) return true;
    const leave = window.confirm("当前标注尚未保存。要放弃修改并切换图像吗？");
    if (leave && state.currentImage) {
      state.draftImageIds.delete(currentImageKey());
    }
    return leave;
  }

  async function selectImage(id, options) {
    if (state.saving || state.deleting || state.switchingFolder || state.directoryStale) return;
    const settings = options || {};
    const nextImage = state.images.find((item) => imageKey(item.id) === imageKey(id));
    if (!nextImage) return;
    if (state.currentImage && imageKey(state.currentImage.id) === imageKey(id)) return;
    if (!settings.skipPrompt && !canDiscardCurrentChanges()) return;

    const sequence = ++state.loadSequence;
    state.currentImage = nextImage;
    state.annotationExists = Boolean(nextImage.annotated);
    state.annotationInvalid = Boolean(nextImage.annotated && nextImage.annotation_valid === false);
    state.revision = null;
    state.dirty = false;
    state.editRevision = 0;
    state.imageStateUncertain = false;
    state.loadingAnnotation = true;
    state.draft = createEmptyAnnotations(state.config);

    clearValidationErrors();
    showBanner("info", "正在读取", "正在加载图像及同名 JSON 标注。");
    setAnnotationState("idle", "读取中");
    setSaveState("", "正在读取标注…");
    elements.currentFileName.textContent = String(nextImage.name || nextImage.id);
    elements.currentFileName.title = String(nextImage.name || nextImage.id);
    updateSelectedListItem();
    updateNavigation();
    updateControls();
    loadPreview(nextImage);

    try {
      const payload = await apiRequest(withDirectoryGeneration(
        `/annotation?image_id=${encodeURIComponent(nextImage.id)}`
      ));
      if (sequence !== state.loadSequence) return;
      state.annotationExists = Boolean(payload && payload.exists);
      state.revision = payload && payload.revision != null ? payload.revision : null;

      if (state.annotationExists) {
        const documentData = payload.document || payload.annotation || {};
        const annotations = documentData.annotations || payload.annotations || {};
        setDraft(annotations, false);
        state.annotationInvalid = false;
        setAnnotationState("saved", "已标注");
        setSaveState("saved", "已加载同名 JSON 标注");
        showBanner("info", "未修改", "当前标注与已保存 JSON 一致。");
      } else {
        state.annotationInvalid = false;
        setDraft(createEmptyAnnotations(state.config), false);
        setAnnotationState("unlabeled", "未标注");
        setSaveState("", "尚未保存标注");
        showBanner("unlabeled", "未标注", "填写并核对各项属性后保存。");
      }
    } catch (error) {
      if (sequence !== state.loadSequence) return;
      if (isDirectoryChangedError(error)) {
        markDirectoryStale(error.payload && error.payload.detail);
        return;
      }
      const isInvalid = nextImage.annotated || error.status === 422;
      state.annotationExists = isInvalid;
      state.annotationInvalid = isInvalid;
      const errorDetail = error.payload && error.payload.detail;
      state.revision = isInvalid && errorDetail && errorDetail.current_revision
        ? String(errorDetail.current_revision)
        : null;
      setDraft(createEmptyAnnotations(state.config), false);
      setAnnotationState("error", isInvalid ? "JSON 异常" : "读取失败");
      setSaveState("error", "标注读取失败，可修正后尝试保存");
      showBanner("error", isInvalid ? "标注文件无法解析" : "标注读取失败",
        `${error.message}${isInvalid ? "；请核对并修复已有 JSON。" : ""}`
      );
      showToast("标注读取失败", error.message, "error", 7500);
    } finally {
      if (sequence !== state.loadSequence) return;
      state.loadingAnnotation = false;
      updateControls();
    }

  }

  function loadPreview(image, retry) {
    clearImageSyncTimer("previewRetryTimer");
    if (!retry) state.previewRetries = 0;
    elements.emptyView.classList.add("hidden");
    elements.imageError.classList.add("hidden");
    elements.viewerLoading.classList.remove("hidden");
    elements.previewImage.hidden = true;
    elements.previewImage.alt = String(image.name || "待标注图像");
    elements.zoomToolbar.hidden = true;
    const expectedId = imageKey(image.id);
    elements.previewImage.dataset.imageId = expectedId;
    elements.previewImage.decoding = "async";
    elements.previewImage.fetchPriority = "high";
    elements.previewImage.src = `${API_ROOT}${withDirectoryGeneration(
      `/image?image_id=${encodeURIComponent(image.id)}&v=${encodeURIComponent(`${image.modified_at || ""}:${image.size || ""}`)}${retry ? `&retry=${state.previewRetries}` : ""}`
    )}`;
  }

  function fitImage() {
    const image = elements.previewImage;
    if (!image.naturalWidth || !image.naturalHeight) return;
    const availableWidth = Math.max(80, elements.imageStage.clientWidth - 48);
    const availableHeight = Math.max(80, elements.imageStage.clientHeight - 64);
    state.fitScale = Math.min(availableWidth / image.naturalWidth, availableHeight / image.naturalHeight, 1);
    state.zoomFactor = 1;
    applyZoom();
  }

  function applyZoom() {
    const image = elements.previewImage;
    if (!image.naturalWidth || !image.naturalHeight) return;
    const scale = state.fitScale * state.zoomFactor;
    image.style.width = `${Math.max(1, Math.round(image.naturalWidth * scale))}px`;
    image.style.height = `${Math.max(1, Math.round(image.naturalHeight * scale))}px`;
    const actualPercent = Math.round(scale * 100);
    elements.zoomText.textContent = state.zoomFactor === 1 ? `适应 ${actualPercent}%` : `${actualPercent}%`;
    elements.zoomOut.disabled = state.zoomFactor <= 0.35;
    elements.zoomIn.disabled = state.zoomFactor >= 6;
  }

  function changeZoom(multiplier) {
    state.zoomFactor = Math.max(0.35, Math.min(6, state.zoomFactor * multiplier));
    applyZoom();
  }

  function normaliseMulti(value, fallback) {
    let values;
    if (Array.isArray(value)) values = value;
    else if (value == null || value === "") values = fallback || ["无"];
    else values = String(value).split(/[、,，/|]/);

    const result = [];
    for (const item of values) {
      const clean = String(item).trim();
      if (clean && !result.includes(clean)) result.push(clean);
    }
    if (!result.length) return Array.from(fallback || ["无"]);
    if (result.length > 1 && result.includes("无")) return result.filter((item) => item !== "无");
    return result;
  }

  function normaliseAnnotations(input) {
    const source = input || {};
    const rackValue = source.rack_level;
    const count = source.food_count;
    const quality = source.quality;
    const deviceModel = source.device_model;
    let foodName = source.food_name == null ? "无" : String(source.food_name).trim();
    if (!foodName) foodName = "无";
    let foodSize = source.food_size;
    if (Array.isArray(foodSize)) {
      foodSize = foodSize.length === 1 ? foodSize[0] : null;
    }
    return {
      food_name: foodName,
      food_count: count == null || count === "" || count === "无" ? "" : String(count).trim(),
      quality: quality == null || quality === "无" ? "" : String(quality).trim(),
      device_model: deviceModel == null || deviceModel === "" || deviceModel === "None"
        ? null
        : String(deviceModel).trim(),
      container_type: normaliseMulti(source.container_type, ["无"]),
      accessory_type: normaliseMulti(source.accessory_type, ["无"]),
      rack_level: normaliseMulti(rackValue, ["无"]),
      food_size: foodSize == null || foodSize === "" || foodSize === "无"
        ? ""
        : String(foodSize).trim()
    };
  }

  function setDraft(annotations, dirty) {
    state.draft = normaliseAnnotations(annotations);
    state.dirty = Boolean(dirty);
    elements.foodName.value = state.draft.food_name;
    elements.foodCount.value = state.draft.food_count;
    elements.quality.value = state.draft.quality;
    renderDeviceModelOptions(state.draft.device_model);
    elements.foodSize.value = state.draft.food_size;
    renderAllChoiceGroups();
    if (dirty && state.currentImage) state.draftImageIds.add(currentImageKey());
    updateDirtyPresentation();
    updateControls();
  }

  function renderAllChoiceGroups() {
    renderChoiceGroup(elements.containerChoices, "container_type", state.options.container_type, state.draft.container_type);
    renderChoiceGroup(elements.accessoryChoices, "accessory_type", state.options.accessory_type, state.draft.accessory_type);
    renderChoiceGroup(elements.rackChoices, "rack_level", state.options.rack_level, state.draft.rack_level);
  }

  function renderChoiceGroup(container, field, options, selectedValues) {
    const selected = normaliseMulti(selectedValues, []);
    const allOptions = Array.from(options);
    for (const value of selected) {
      if (!allOptions.includes(value)) allOptions.push(value);
    }
    const fragment = document.createDocumentFragment();
    allOptions.forEach((value, index) => {
      const label = document.createElement("label");
      label.className = "choice-chip";
      if (!options.includes(value)) label.classList.add("choice-chip--invalid");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.name = field;
      checkbox.value = value;
      checkbox.checked = selected.includes(value);
      checkbox.id = `${field}-${index}`;
      checkbox.addEventListener("change", () => handleChoiceChange(container, checkbox));
      const text = document.createElement("span");
      text.textContent = options.includes(value) ? value : `${value}（无效）`;
      label.append(checkbox, text);
      fragment.append(label);
    });
    container.replaceChildren(fragment);
  }

  function handleChoiceChange(container, changed) {
    const checkboxes = Array.from(container.querySelectorAll("input[type='checkbox']"));
    if (changed.checked && changed.value === "无") {
      checkboxes.forEach((checkbox) => { if (checkbox !== changed) checkbox.checked = false; });
    } else if (changed.checked) {
      const none = checkboxes.find((checkbox) => checkbox.value === "无");
      if (none) none.checked = false;
    }
    if (!checkboxes.some((checkbox) => checkbox.checked)) {
      const none = checkboxes.find((checkbox) => checkbox.value === "无");
      if (none) none.checked = true;
    }
    syncDraftFromForm();
    markDirty();
  }

  function selectedValues(container) {
    return Array.from(container.querySelectorAll("input[type='checkbox']:checked"))
      .map((input) => input.value);
  }

  function syncDraftFromForm() {
    state.draft.food_name = elements.foodName.value.trim();
    state.draft.food_count = elements.foodCount.value.trim();
    state.draft.quality = elements.quality.value.trim();
    state.draft.device_model = elements.deviceModel.value || null;
    state.draft.container_type = selectedValues(elements.containerChoices);
    state.draft.accessory_type = selectedValues(elements.accessoryChoices);
    state.draft.rack_level = selectedValues(elements.rackChoices);
    state.draft.food_size = elements.foodSize.value.trim();
  }

  function markDirty() {
    if (!state.currentImage || state.loadingAnnotation) return;
    state.editRevision += 1;
    state.dirty = true;
    state.draftImageIds.add(currentImageKey());
    clearValidationErrors();
    updateDirtyPresentation();
    applyListFilters();
    updateControls();
  }

  function updateDirtyPresentation() {
    if (!state.currentImage) return;
    if (state.dirty) {
      const statusTitle = state.saving
        ? "保存中有新修改"
        : (state.annotationInvalid
          ? "修复待保存"
          : (state.annotationExists ? "修改待保存" : "新标注待保存"));
      const statusText = state.saving
        ? "正在保存点击按钮时的版本；保存期间的新修改会保留在页面中，稍后需要再次保存。"
        : (state.annotationInvalid
          ? "当前修复尚未写入 JSON，保存成功后才会替换异常标注。"
          : (state.annotationExists
            ? "当前修改尚未写入 JSON，保存成功后才会更新已有标注。"
            : "当前内容尚未写入 JSON，保存成功后才算已标注。"));
      setAnnotationState("draft", statusTitle);
      setSaveState(
        state.saving ? "saving" : "dirty",
        state.saving
          ? "保存进行中；最新修改尚未包含在本次保存中"
          : (state.annotationExists ? "已有未保存的修改" : "新标注尚未保存")
      );
      showBanner("draft", statusTitle, statusText);
    } else if (state.annotationExists && !state.annotationInvalid) {
      setAnnotationState("saved", "已标注");
    }
  }

  function setAnnotationState(mode, text) {
    elements.annotationState.className = `annotation-state annotation-state--${mode}`;
    elements.annotationState.textContent = text;
  }

  function setSaveState(mode, text) {
    elements.saveState.className = `save-state${mode ? ` save-state--${mode}` : ""}`;
    elements.saveState.textContent = text;
  }

  function showBanner(type, title, text) {
    elements.statusBanner.className = `status-banner${type && type !== "info" ? ` status-banner--${type}` : ""}`;
    elements.statusBannerTitle.textContent = title;
    elements.statusBannerText.textContent = text;
    elements.statusBanner.classList.remove("hidden");
  }

  function clearValidationErrors() {
    const ids = ["foodNameError", "foodCountError", "qualityError", "deviceModelError", "containerTypeError", "accessoryTypeError", "rackLevelError", "foodSizeError"];
    ids.forEach((id) => { byId(id).textContent = ""; });
    elements.form.querySelectorAll(".has-error").forEach((node) => node.classList.remove("has-error"));
    elements.form.querySelectorAll("[aria-invalid='true']").forEach((node) => {
      node.removeAttribute("aria-invalid");
    });
  }

  function setFieldError(errorId, message, focusElement) {
    const error = byId(errorId);
    error.textContent = message;
    const field = error.closest(".field");
    if (field) field.classList.add("has-error");
    if (focusElement) focusElement.setAttribute("aria-invalid", "true");
    const focusTarget = focusElement && focusElement.matches("input, select, textarea, button")
      ? focusElement
      : (focusElement && focusElement.querySelector("input, select, button"));
    if (focusTarget) focusTarget.setAttribute("aria-invalid", "true");
    return focusTarget || focusElement;
  }

  function validateDraft() {
    syncDraftFromForm();
    clearValidationErrors();
    let firstInvalid = null;

    if (!state.draft.food_name) {
      firstInvalid = setFieldError("foodNameError", "请填写主要食物名称。", elements.foodName);
    }

    if (state.draft.food_count !== "") {
      const count = Number(state.draft.food_count);
      if (!Number.isInteger(count) || count < 0) {
        firstInvalid = firstInvalid || setFieldError("foodCountError", "个数必须是大于或等于 0 的整数。", elements.foodCount);
      }
    }

    if (state.draft.quality !== "") {
      const quality = Number(state.draft.quality);
      if (!Number.isFinite(quality)) {
        firstInvalid = firstInvalid || setFieldError("qualityError", "总重量必须是数字。", elements.quality);
      }
    }

    if (state.draft.device_model !== null && !state.options.device_model.includes(state.draft.device_model)) {
      firstInvalid = firstInvalid || setFieldError("deviceModelError", "请选择有效的设备型号。", elements.deviceModel);
    }

    const groups = [
      ["container_type", state.options.container_type, "containerTypeError", elements.containerChoices],
      ["accessory_type", state.options.accessory_type, "accessoryTypeError", elements.accessoryChoices],
      ["rack_level", state.options.rack_level, "rackLevelError", elements.rackChoices]
    ];

    for (const [field, allowed, errorId, target] of groups) {
      const values = state.draft[field];
      let message = "";
      if (!values.length) message = "请至少选择一个值；无法判断请选择“无”。";
      else if (values.includes("无") && values.length > 1) message = "“无”不能与其他值同时选择。";
      else {
        const unknown = values.find((value) => !allowed.includes(value));
        if (unknown) message = `“${unknown}”不在允许的枚举中。`;
      }
      if (message) firstInvalid = firstInvalid || setFieldError(errorId, message, target);
    }

    if (state.draft.food_size !== "") {
      const foodSize = Number(state.draft.food_size);
      if (!Number.isInteger(foodSize) || foodSize <= 0) {
        firstInvalid = firstInvalid || setFieldError("foodSizeError", "尺寸必须是大于 0 的整数。", elements.foodSize);
      }
    }

    if (firstInvalid) {
      firstInvalid.scrollIntoView({ behavior: "smooth", block: "center" });
      if (typeof firstInvalid.focus === "function") firstInvalid.focus({ preventScroll: true });
      return false;
    }
    return true;
  }

  function serialiseAnnotations() {
    const nullableInteger = (value) => value === "" ? null : Number(value);
    const nullableFloat = (value) => value === "" ? null : Number(value);
    return {
      food_name: String(state.draft.food_name),
      food_count: nullableInteger(state.draft.food_count),
      quality: nullableFloat(state.draft.quality),
      device_model: state.draft.device_model,
      container_type: state.draft.container_type.map(String),
      accessory_type: state.draft.accessory_type.map(String),
      rack_level: state.draft.rack_level.map(String),
      food_size: nullableInteger(state.draft.food_size)
    };
  }

  async function saveAnnotation(moveNext) {
    if (!state.currentImage || state.saving || state.deleting || state.imageStateUncertain || state.loadingAnnotation || state.switchingFolder || state.directoryStale) return false;
    if (!state.dirty && state.annotationExists && !state.annotationInvalid) {
      if (moveNext) await moveRelative(1);
      return true;
    }
    if (!validateDraft()) {
      showToast("标注尚未保存", "请先修正表单中标出的字段。", "warning");
      return false;
    }

    const imageId = state.currentImage.id;
    const sequence = state.loadSequence;
    const editRevisionAtStart = state.editRevision;
    const beforeSaveIndex = currentFilteredIndex();
    const nextImageId = moveNext && beforeSaveIndex >= 0 && state.filteredImages[beforeSaveIndex + 1]
      ? state.filteredImages[beforeSaveIndex + 1].id
      : null;
    const annotations = serialiseAnnotations();
    state.saving = true;
    state.imageListRevision += 1;
    updateControls();
    setSaveState("saving", "正在保存 JSON…");
    showBanner("info", "保存中", "正在写入 JSON，请稍候。");

    try {
      const result = await apiRequest(withDirectoryGeneration(
        `/annotation?image_id=${encodeURIComponent(imageId)}`
      ), {
        method: "PUT",
        body: JSON.stringify({ annotations, revision: state.revision })
      });
      if (sequence !== state.loadSequence) return false;

      state.revision = result && result.revision != null ? result.revision : state.revision;
      state.annotationExists = true;
      state.annotationInvalid = false;
      state.currentImage.annotated = true;
      state.currentImage.annotation_valid = true;
      const editedWhileSaving = state.editRevision !== editRevisionAtStart;
      const savedDocument = result && (result.document || result.annotation);
      if (editedWhileSaving) {
        state.dirty = true;
        state.draftImageIds.add(currentImageKey());
        showBanner(
          "warning",
          "本次保存成功，仍有新修改",
          "保存期间输入的新内容已保留在页面中，但尚未写入 JSON，请再次保存。"
        );
        setAnnotationState("draft", "新修改待保存");
        setSaveState("dirty", "本次保存成功；保存期间的新修改尚未保存");
      } else {
        state.draftImageIds.delete(currentImageKey());
        setDraft(savedDocument && savedDocument.annotations ? savedDocument.annotations : annotations, false);
        showBanner("success", "未修改", "JSON 已保存，当前没有未保存修改。");
        setAnnotationState("saved", "已标注");
        setSaveState("saved", "JSON 已保存到图像同目录");
      }
      updateStats(true);
      applyListFilters();
      showToast(
        editedWhileSaving ? "已保存较早版本" : "保存成功",
        editedWhileSaving
          ? "保存期间的新修改仍在页面中，请再次保存。"
          : String(state.currentImage.name || imageId),
        editedWhileSaving ? "warning" : "success",
        editedWhileSaving ? 6500 : undefined
      );

      if (moveNext && nextImageId != null && !editedWhileSaving) {
        state.saving = false;
        updateControls();
        await selectImage(nextImageId, { skipPrompt: true });
      }
      return true;
    } catch (error) {
      if (sequence !== state.loadSequence) return false;
      if (isDirectoryChangedError(error)) {
        markDirectoryStale(error.payload && error.payload.detail);
        return false;
      }
      const conflict = error.status === 409;
      setSaveState("error", conflict ? "文件已发生变化，请重新加载后再保存" : "保存失败，修改仍保留在页面中");
      showBanner("error", conflict ? "保存冲突" : "保存失败",
        conflict ? "同名 JSON 已被其他操作更新。请切换图片后重新进入，核对最新标注。" : error.message
      );
      showToast(conflict ? "保存冲突" : "保存失败", error.message, "error", 8500);
      return false;
    } finally {
      state.saving = false;
      state.imageListRevision += 1;
      updateControls();
    }
  }

  function currentFilteredIndex() {
    return state.filteredImages.findIndex((item) => imageKey(item.id) === currentImageKey());
  }

  async function moveRelative(direction, options) {
    if (
      state.loadingAnnotation
      || state.saving
      || state.deleting
      || state.switchingFolder
      || state.directoryStale
      || !state.currentImage
      || !state.filteredImages.length
    ) return;
    const index = currentFilteredIndex();
    if (index < 0) return;
    const target = state.filteredImages[index + direction];
    if (!target) return;
    await selectImage(target.id, options);
  }

  function updateNavigation() {
    const index = currentFilteredIndex();
    const total = state.filteredImages.length;
    elements.positionText.textContent = index >= 0 ? `${index + 1} / ${total}` : `0 / ${total}`;
    const busy = state.loadingAnnotation || state.saving || state.deleting || state.switchingFolder || state.directoryStale;
    elements.previousButton.disabled = busy || index <= 0;
    elements.nextButton.disabled = busy || index < 0 || index >= total - 1;
  }

  function updateControls() {
    const hasImage = Boolean(state.currentImage);
    const fieldBusy = !hasImage || state.loadingAnnotation || state.deleting || state.switchingFolder;
    const actionBusy = fieldBusy || state.saving || state.directoryStale || state.imageStateUncertain;
    const canSave = state.dirty || (hasImage && !state.annotationExists);
    elements.fieldset.disabled = fieldBusy;
    elements.saveButton.disabled = actionBusy || !canSave;
    elements.saveNextButton.disabled = actionBusy || !canSave;
    elements.deleteImageButton.disabled = actionBusy || state.downloading;
    elements.deleteImageButton.textContent = state.deleting ? "正在删除…" : "删除当前图像";
    elements.deleteImageButton.setAttribute("aria-busy", String(state.deleting));
    elements.downloadAnnotatedButton.disabled = state.downloading || state.deleting || state.saving || state.switchingFolder || state.directoryStale || state.directoryGeneration == null;
    elements.downloadAnnotatedButton.textContent = state.downloading ? "正在打包…" : "下载已标注数据";
    elements.downloadAnnotatedButton.setAttribute("aria-busy", String(state.downloading));
    const statisticsDirectoryPending = state.directoryGeneration == null;
    elements.openStatisticsButton.disabled = state.switchingFolder
      || state.browsingFolder
      || state.directoryStale
      || statisticsDirectoryPending;
    elements.openStatisticsButton.setAttribute(
      "aria-busy",
      state.switchingFolder || statisticsDirectoryPending ? "true" : "false"
    );
    elements.openStatisticsButton.title = statisticsDirectoryPending
      ? "正在连接数据目录…"
      : state.switchingFolder
        ? "正在切换数据目录…"
        : state.directoryStale
          ? "数据目录已切换，请重新选择文件夹或刷新页面"
          : "查看当前文件夹的属性值分布";
    elements.refreshStatisticsButton.disabled = state.statisticsLoading || state.switchingFolder || state.directoryStale;
    elements.chooseDataDirButton.disabled = state.switchingFolder || state.browsingFolder || state.loadingAnnotation || state.saving || state.deleting || state.downloading;
    elements.search.disabled = state.deleting || state.switchingFolder || state.directoryStale;
    elements.filter.disabled = state.deleting || state.switchingFolder || state.directoryStale;
    for (const item of elements.imageList.querySelectorAll(".image-item")) {
      item.disabled = state.saving || state.deleting || state.switchingFolder || state.directoryStale;
    }
    updateNavigation();
  }

  function showToast(title, message, type, duration) {
    const toast = document.createElement("div");
    toast.className = `toast toast--${type || "info"}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");
    const copy = document.createElement("div");
    copy.className = "toast-copy";
    const heading = document.createElement("strong");
    heading.textContent = title;
    const text = document.createElement("p");
    text.textContent = message || "";
    copy.append(heading, text);
    const close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "关闭提示");
    close.textContent = "×";
    close.addEventListener("click", () => toast.remove());
    toast.append(copy, close);
    elements.toastRegion.append(toast);
    window.setTimeout(() => toast.remove(), duration || 4200);
  }

  function isFormControl(target) {
    return target instanceof HTMLElement && Boolean(target.closest("input, select, textarea, button, [contenteditable='true']"));
  }

  function isTextEntryControl(target) {
    return target instanceof Element && (target.isContentEditable || Boolean(target.closest("input, select, textarea, [contenteditable]:not([contenteditable='false']), [role='textbox']")));
  }

  function bindEvents() {
    elements.deleteImageButton.addEventListener("click", deleteCurrentImage);
    elements.downloadAnnotatedButton.addEventListener("click", downloadAnnotatedData);
    elements.openStatisticsButton.addEventListener("click", openStatistics);
    elements.closeStatisticsButton.addEventListener("click", closeStatistics);
    elements.refreshStatisticsButton.addEventListener("click", loadStatistics);
    elements.statisticsDialog.addEventListener("click", (event) => {
      if (event.target !== elements.statisticsDialog) return;
      const bounds = elements.statisticsDialog.getBoundingClientRect();
      const outside = event.clientX < bounds.left
        || event.clientX > bounds.right
        || event.clientY < bounds.top
        || event.clientY > bounds.bottom;
      if (outside) closeStatistics();
    });
    elements.statisticsDialog.addEventListener("close", () => {
      document.body.classList.remove("dialog-open");
    });
    elements.chooseDataDirButton.addEventListener("click", openDataDirectoryDialog);
    elements.dataDirectoryForm.addEventListener("submit", chooseDataDirectory);
    elements.dataDirectoryGoButton.addEventListener("click", browseEnteredDataDirectory);
    elements.dataDirectoryPathInput.addEventListener("input", () => {
      if (elements.dataDirectoryPathInput.value.trim() === state.directorySelectedPath) return;
      state.directorySelectedPath = "";
      elements.selectedDataDirectoryText.textContent = "请转到该地址后选择";
      for (const item of elements.dataDirectoryList.querySelectorAll(".directory-browser-item")) {
        item.classList.remove("directory-browser-item--selected");
        item.setAttribute("aria-selected", "false");
      }
      elements.dataDirectoryError.textContent = "";
      updateDirectoryBrowserControls();
    });
    elements.dataDirectoryPathInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      browseEnteredDataDirectory();
    });
    elements.dataDirectoryUpButton.addEventListener("click", () => {
      browseDataDirectories(state.directoryBrowseParentPath || null);
    });
    elements.dataDirectoryRefreshButton.addEventListener("click", () => {
      browseDataDirectories(state.directoryBrowseMode === "roots" ? null : state.directoryBrowsePath);
    });
    elements.cancelDataDirectoryButton.addEventListener("click", closeDataDirectoryDialog);
    elements.cancelDataDirectoryIcon.addEventListener("click", closeDataDirectoryDialog);
    elements.dataDirectoryDialog.addEventListener("cancel", (event) => {
      if (state.switchingFolder || state.browsingFolder) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      closeDataDirectoryDialog();
    });
    elements.dataDirectoryDialog.addEventListener("click", (event) => {
      if (event.target === elements.dataDirectoryDialog) closeDataDirectoryDialog();
    });
    elements.dataDirectoryDialog.addEventListener("close", () => {
      document.body.classList.remove("dialog-open");
    });
    elements.search.addEventListener("input", applyListFilters);
    elements.filter.addEventListener("change", applyListFilters);
    elements.previousButton.addEventListener("click", () => moveRelative(-1));
    elements.nextButton.addEventListener("click", () => moveRelative(1));
    elements.saveButton.addEventListener("click", () => saveAnnotation(false));
    elements.saveNextButton.addEventListener("click", () => saveAnnotation(true));
    elements.zoomOut.addEventListener("click", () => changeZoom(1 / 1.2));
    elements.zoomIn.addEventListener("click", () => changeZoom(1.2));
    elements.fitButton.addEventListener("click", fitImage);
    for (const input of [elements.foodName, elements.foodCount, elements.quality, elements.foodSize]) {
      input.addEventListener("input", () => {
        syncDraftFromForm();
        markDirty();
      });
    }
    elements.deviceModel.addEventListener("change", () => {
      syncDraftFromForm();
      markDirty();
    });

    elements.previewImage.addEventListener("load", () => {
      if (!state.currentImage || elements.previewImage.dataset.imageId !== currentImageKey()) return;
      clearImageSyncTimer("previewRetryTimer");
      elements.viewerLoading.classList.add("hidden");
      elements.imageError.classList.add("hidden");
      elements.previewImage.hidden = false;
      elements.zoomToolbar.hidden = false;
      fitImage();
    });

    elements.previewImage.addEventListener("error", () => {
      if (!state.currentImage || elements.previewImage.dataset.imageId !== currentImageKey() || state.deleting) return;
      if (!state.directoryStale && state.previewRetries < 3) {
        state.previewRetries += 1;
        const imageId = currentImageKey();
        state.previewRetryTimer = window.setTimeout(() => {
          state.previewRetryTimer = null;
          if (currentImageKey() === imageId && !state.directoryStale) loadPreview(state.currentImage, true);
        }, 300 * state.previewRetries);
        scheduleSilentImageSync({ delay: 0 });
        return;
      }
      elements.viewerLoading.classList.add("hidden");
      elements.previewImage.hidden = true;
      elements.zoomToolbar.hidden = true;
      elements.imageError.classList.remove("hidden");
      if (state.directoryStale) return;
      showToast("图像加载失败", String(state.currentImage && state.currentImage.name || "请检查文件"), "error");
    });

    window.addEventListener("resize", () => {
      if (!elements.previewImage.hidden && state.zoomFactor === 1) fitImage();
    });

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        stopImageRealtimeSync();
        return;
      }
      startImageRealtimeSync();
      scheduleSilentImageSync({ delay: 0, autoSelectFirst: true });
    });

    window.addEventListener("pagehide", stopImageRealtimeSync);

    window.addEventListener("beforeunload", (event) => {
      if (!state.dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });

    document.addEventListener("keydown", (event) => {
      if (event.isComposing) return;
      if (elements.dataDirectoryDialog.open) return;
      const modifier = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (elements.statisticsDialog.open) {
        const reservedShortcut = (modifier && (key === "s" || event.key === "Enter"))
          || (event.altKey && (event.key === "ArrowLeft" || event.key === "ArrowRight"));
        if (reservedShortcut) event.preventDefault();
        return;
      }
      if (modifier && key === "s") {
        event.preventDefault();
        saveAnnotation(false);
        return;
      }
      if (modifier && event.key === "Enter") {
        event.preventDefault();
        saveAnnotation(true);
        return;
      }
      if (event.altKey && event.key === "ArrowLeft") {
        event.preventDefault();
        moveRelative(-1);
        return;
      }
      if (event.altKey && event.key === "ArrowRight") {
        event.preventDefault();
        moveRelative(1);
        return;
      }
      if (!modifier && !event.altKey && !event.repeat && !isTextEntryControl(event.target)) {
        if (key === "d") {
          event.preventDefault();
          deleteCurrentImage();
          return;
        }
        if (key === "q") {
          event.preventDefault();
          moveRelative(-1);
          return;
        }
        if (key === "w") {
          event.preventDefault();
          moveRelative(1);
          return;
        }
        if (key === "e") {
          event.preventDefault();
          saveAnnotation(true);
          return;
        }
      }
      if (event.key === "Escape" && !isFormControl(event.target)) {
        fitImage();
      }
    });
  }

  async function initialise() {
    bindEvents();
    renderSchemaContract();
    renderDeviceModelOptions(state.draft.device_model);
    renderAllChoiceGroups();
    updateControls();
    await loadConfig();
    renderAllChoiceGroups();
    await loadImages();
    startImageRealtimeSync();
    updateControls();
    checkHealth();
  }

  initialise();
})();
