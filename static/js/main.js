/* MovieSync 主页面 JavaScript
 * 用途：负责影视列表加载/渲染、搜索、选择影片、资源候选弹窗、白名单转存、日志、频道状态和自动追剧。
 * 维护说明：apiFetch() 统一携带同源 Cookie 与 CSRF；当前版本还提供 getNoCoverImage()，避免无海报时因缺少函数导致整页渲染中断。 */
let currentTag = '电影';
let currentSort = 'U';
let currentMetadataProviderId = '';

let moviesData = [];
let currentChaseSelectedCandidate = null;
let candidateModalState = null;
let chaseCandidateState = [];
let movieViewRequestSeq = 0;
let categoryOptionsRequestSeq = 0;
let candidateSearchRequestSeq = 0;
const metadataListCache = new Map();
const metadataListRequests = new Map();
const METADATA_LIST_CACHE_TTL_MS = 90_000;
let candidateSearchProgressText = '';
let candidateSearchProgressTimer = null;

function startCandidateSearchProgress(requestId) {
    const startedAt = Date.now();
    const update = () => {
        if (requestId !== candidateSearchRequestSeq) return;
        const seconds = Math.floor((Date.now() - startedAt) / 1000);
        const elapsed = seconds < 1 ? '刚刚开始' : `已等待 ${seconds} 秒`;
        candidateSearchProgressText = seconds < 4
            ? `${elapsed}：正在并发查询已启用的资源来源，并筛选可用分享链接。首批结果返回后会立即显示。`
            : `${elapsed}：首批候选仍在生成中，可能有资源来源正在等待上游响应或解析网盘分享。搜索仍在进行；结果返回后会自动显示，随后继续补充更多资源。`;
        const progress = document.getElementById('candidate-search-progress');
        if (progress) progress.textContent = candidateSearchProgressText;
        const emptyState = document.querySelector('#candidate-content .candidate-search-empty-state');
        if (emptyState) emptyState.textContent = candidateSearchProgressText;
    };
    if (candidateSearchProgressTimer) window.clearInterval(candidateSearchProgressTimer);
    update();
    candidateSearchProgressTimer = window.setInterval(update, 1000);
}

function stopCandidateSearchProgress(requestId) {
    if (requestId !== candidateSearchRequestSeq) return;
    if (candidateSearchProgressTimer) {
        window.clearInterval(candidateSearchProgressTimer);
        candidateSearchProgressTimer = null;
    }
}

function metadataListCacheKey(tag, sort, providerId) {
    return [tag, sort, providerId || ''].join('::');
}

async function loadMetadataList(tag, sort, providerId = '') {
    const key = metadataListCacheKey(tag, sort, providerId);
    const cached = metadataListCache.get(key);
    if (cached && Date.now() - cached.savedAt < METADATA_LIST_CACHE_TTL_MS) {
        return cached.movies;
    }
    if (metadataListRequests.has(key)) return metadataListRequests.get(key);

    const request = (async () => {
        const params = new URLSearchParams({ tag, sort });
        if (providerId) params.set('provider_id', providerId);
        const response = await apiFetch('/api/get-movies?' + params.toString());
        const result = await response.json();
        if (!response.ok || !result.success) {
            throw new Error(result.message || '加载影视列表失败');
        }
        const movies = Array.isArray(result.movies) ? result.movies : [];
        metadataListCache.set(key, { movies, savedAt: Date.now() });
        return movies;
    })();
    metadataListRequests.set(key, request);
    try {
        return await request;
    } finally {
        if (metadataListRequests.get(key) === request) metadataListRequests.delete(key);
    }
}

async function preloadMetadataCategories(activeTag) {
    // 豆瓣仍是主要数据源。首屏显示后在后台顺序预热其他分类，避免同时压满同一 HTTP 客户端。
    for (const tag of ['电影', '电视剧', '综艺', '动漫']) {
        if (tag === activeTag) continue;
        const key = metadataListCacheKey(tag, currentSort, currentMetadataProviderId);
        const cached = metadataListCache.get(key);
        if (cached && Date.now() - cached.savedAt < METADATA_LIST_CACHE_TTL_MS) continue;
        try {
            await loadMetadataList(tag, currentSort, currentMetadataProviderId);
        } catch (err) {
            console.debug('后台预热影视分类失败:', tag, err);
        }
    }
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>\"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[char]));
}

async function apiFetch(url, options = {}) {
    const init = { ...options, credentials: 'same-origin' };
    const method = (init.method || 'GET').toUpperCase();
    const headers = new Headers(init.headers || {});
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
        const token = document.querySelector('meta[name=csrf-token]')?.content;
        if (token) headers.set('X-CSRF-Token', token);
    }
    init.headers = headers;
    const response = await window.fetch(url, init);
    if (response.status === 401) {
        window.location.href = '/login';
    }
    return response;
}

async function loadMetadataProviders() {
    const select = document.getElementById('metadata-provider-select');
    if (!select) return;
    try {
        const response = await apiFetch('/api/cards');
        const result = await response.json();
        if (!result.success || !Array.isArray(result.cards)) return;
        const providers = result.cards
            .filter(card => card.type === 'metadata_provider' && card.enabled !== false)
            .sort((a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id)));
        select.replaceChildren(new Option('自动来源', ''));
        for (const provider of providers) {
            const requiresConfig = Array.isArray(provider.config_fields)
                && provider.config_fields.some(field => field.required);
            const label = requiresConfig && provider.configured === false
                ? `${provider.name || provider.id}（未配置）`
                : (provider.name || provider.id);
            select.add(new Option(label, provider.id));
        }
        if (providers.some(provider => provider.id === currentMetadataProviderId)) {
            select.value = currentMetadataProviderId;
        } else {
            currentMetadataProviderId = '';
            select.value = '';
        }
    } catch (err) {
        console.warn('读取影视数据源列表失败，将使用自动来源:', err);
    }
}

function onMetadataProviderChange() {
    currentMetadataProviderId = document.getElementById('metadata-provider-select')?.value || '';
    const query = document.getElementById('search-input')?.value.trim();
    if (query) doSearch();
    else fetchMovies();
}

let logTimerInterval = null;
let logTimerSeconds = 0;
let logAutoCloseTimer = null;

document.addEventListener("DOMContentLoaded", () => {
    loadMetadataProviders().finally(() => fetchMovies());
    loadCategoryOptions();
    document.getElementById('batch-storage-target')?.addEventListener('change', event => {
        const targetId = String(event.target.value || '');
        window.defaultStorageTargetId = targetId;
        loadCategoryDestinations(targetId);
        const modal = document.getElementById('candidate-modal');
        const movies = candidateModalState?.movies;
        if (targetId && Array.isArray(movies) && movies.length && modal && !modal.classList.contains('hidden')) {
            // Changing the destination also changes which cloud types PanSou searches.
            searchAndOpenCandidates(movies, targetId, true);
        }
    });
    applyGlassmorphismStyles();
});

function applyGlassmorphismStyles() {
    const topNavs = document.querySelectorAll('header, nav');
    topNavs.forEach(el => {
        el.classList.add('backdrop-blur-md', 'bg-slate-900/80', 'border-b', 'border-slate-800/80');
    });
}

async function loadCategoryOptions() {
    const requestId = ++categoryOptionsRequestSeq;
    const targetSelect = document.getElementById('batch-storage-target');
    const selects = document.querySelectorAll('.global-category-select');
    try {
        const targetResp = await apiFetch('/api/storage-targets');
        const targetData = await targetResp.json();
        if (requestId !== categoryOptionsRequestSeq) return;
        if (!targetResp.ok || !targetData.success) throw new Error(targetData.message || '读取存储卡片失败');

        const targets = (Array.isArray(targetData.targets) ? targetData.targets : [])
            .filter(target => target && target.id && target.enabled !== false && !target.config_error);
        window.storageTargets = targets;
        if (targetSelect) {
            const previousTargetId = String(targetSelect.value || '');
            const configuredDefault = String(targetData.default_target_id || '');
            targetSelect.replaceChildren();
            for (const target of targets) {
                targetSelect.add(new Option(target.name || target.id, target.id));
            }
            const preferred = targets.find(target => target.id === previousTargetId)
                || targets.find(target => target.id === configuredDefault)
                || (!configuredDefault && targets.length === 1 ? targets[0] : null);
            if (configuredDefault && !targets.some(target => target.id === configuredDefault)) {
                targetSelect.add(new Option('默认存储卡片不可用，请手动选择', ''));
                targetSelect.value = '';
            } else if (preferred) {
                targetSelect.value = preferred.id;
            } else {
                targetSelect.add(new Option('请选择存储卡片', ''));
                targetSelect.value = '';
            }
            targetSelect.disabled = !targets.length;
        }

        const targetId = String(targetSelect?.value || targetData.default_target_id || '');
        if (!targetId || !targets.some(target => target.id === targetId)) {
            window.defaultStorageTargetId = '';
            selects.forEach(selectEl => {
                selectEl.replaceChildren(new Option(
                    targets.length ? '请先选择存储卡片' : '没有可用的存储卡片',
                    ''
                ));
                selectEl.disabled = true;
            });
            return;
        }
        window.defaultStorageTargetId = targetId;
        await loadCategoryDestinations(targetId, requestId);
    } catch (err) {
        if (requestId !== categoryOptionsRequestSeq) return;
        window.defaultStorageTargetId = '';
        if (targetSelect) {
            targetSelect.replaceChildren(new Option('读取存储卡片失败，请刷新重试', ''));
            targetSelect.disabled = true;
        }
        selects.forEach(selectEl => {
            selectEl.replaceChildren(new Option('读取保存目录失败，请刷新重试', ''));
            selectEl.disabled = true;
        });
        console.error("读取存储卡片或保存目录失败:", err);
    }
}

async function loadCategoryDestinations(targetId, requestId = ++categoryOptionsRequestSeq) {
    const selects = document.querySelectorAll('.global-category-select');
    if (!targetId) {
        selects.forEach(selectEl => {
            selectEl.replaceChildren(new Option('请先选择存储卡片', ''));
            selectEl.disabled = true;
        });
        window.defaultStorageTargetId = '';
        return;
    }
    selects.forEach(selectEl => {
        selectEl.replaceChildren(new Option('正在读取保存目录…', ''));
        selectEl.disabled = true;
    });
    try {
        const destResp = await apiFetch('/api/storage-targets/' + encodeURIComponent(targetId) + '/destinations');
        const destData = await destResp.json();
        if (requestId !== categoryOptionsRequestSeq) return;
        if (!destResp.ok || !destData.success) throw new Error(destData.message || '读取保存目录失败');
        const options = Array.isArray(destData.destinations) ? destData.destinations : [];
        selects.forEach(selectEl => {
            selectEl.replaceChildren();
            if (!options.length) {
                selectEl.add(new Option('该存储卡片没有可用目录', ''));
                selectEl.disabled = true;
                return;
            }
            for (const item of options) {
                const opt = document.createElement('option');
                opt.value = item.id;
                opt.textContent = item.name || item.id;
                opt.dataset.category = item.category || '';
                selectEl.appendChild(opt);
            }
            const matched = options.find(item => item.category === currentTag)
                || options.find(item => item.category === '电视剧' && selectEl.id === 'chase-target-fid')
                || options.find(item => item.is_default)
                || options[0];
            if (matched) selectEl.value = matched.id;
            selectEl.disabled = false;
        });
        window.defaultStorageTargetId = targetId;
    } catch (err) {
        if (requestId !== categoryOptionsRequestSeq) return;
        selects.forEach(selectEl => {
            selectEl.replaceChildren(new Option('读取目录失败，请刷新重试', ''));
            selectEl.disabled = true;
        });
        console.error("读取保存目录失败:", err);
    }
}

function changeTag(tag) {
    currentTag = tag;
    document.querySelectorAll('.nav-tag').forEach(btn => {
        const active = btn.dataset.tag === tag;
        btn.className = 'nav-tag px-3.5 py-1.5 rounded-xl text-sm font-medium transition ' +
            (active
                ? 'bg-blue-600/90 text-white shadow-lg shadow-blue-600/10'
                : 'glass-tab bg-slate-900/50 text-slate-300 hover:bg-slate-800/80');
    });
    loadCategoryOptions();
    fetchMovies();
}

function changeSort(sort) {
    currentSort = sort;
    document.querySelectorAll('.sort-tag').forEach(btn => {
        if (btn.dataset.sort === sort) {
            btn.className = "sort-tag px-3 py-1 rounded-md font-medium transition bg-blue-600 text-white shadow";
        } else {
            btn.className = "sort-tag px-3 py-1 rounded-md font-medium transition text-slate-400 hover:text-white";
        }
    });
    fetchMovies();
}

async function fetchMovies() {
    const requestId = ++movieViewRequestSeq;
    const loading = document.getElementById('loading');
    const grid = document.getElementById('movie-grid');
    loading.classList.remove('hidden');
    grid.classList.add('hidden');

    try {
        const movies = await loadMetadataList(currentTag, currentSort, currentMetadataProviderId);
        if (requestId !== movieViewRequestSeq) return;

        moviesData = movies.map(movie => ({
            ...movie,
            tag: movie.tag || currentTag
        }));
        renderGrid();
        updateEmptyState();
        // 分类预热在首屏绘制之后执行，不阻塞当前页面。
        preloadMetadataCategories(currentTag);
    } catch (err) {
        if (requestId === movieViewRequestSeq) {
            console.error(err);
            showToast(err.message || '影片加载失败，请稍后重试', 'error');
        }
    } finally {
        if (requestId === movieViewRequestSeq) {
            loading.classList.add('hidden');
            grid.classList.remove('hidden');
        }
    }
}

async function doSearch() {
    const query = document.getElementById('search-input')?.value.trim() || '';
    if (!query) {
        fetchMovies();
        return;
    }

    const requestId = ++movieViewRequestSeq;
    const loading = document.getElementById('loading');
    const grid = document.getElementById('movie-grid');
    loading.classList.remove('hidden');
    grid.classList.add('hidden');

    try {
        const params = new URLSearchParams({ q: query, tag: currentTag });
        if (currentMetadataProviderId) params.set('provider_id', currentMetadataProviderId);
        const resp = await apiFetch(`/api/search-metadata?${params.toString()}`);
        const res = await resp.json();

        if (requestId !== movieViewRequestSeq) return;

        if (res.success) {
            moviesData = Array.isArray(res.movies) ? res.movies : [];
            renderGrid();
            updateEmptyState();
        } else if (requestId === movieViewRequestSeq) {
            showToast(res.message || '搜索失败', 'error');
        }
    } catch (err) {
        if (requestId === movieViewRequestSeq) {
            console.error(err);
            showToast('搜索失败，请稍后重试', 'error');
        }
    } finally {
        if (requestId === movieViewRequestSeq) {
            loading.classList.add('hidden');
            grid.classList.remove('hidden');
        }
    }
}


function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    const icon = type === 'error'
        ? 'fa-circle-exclamation'
        : type === 'success'
            ? 'fa-circle-check'
            : 'fa-circle-info';

    toast.className = 'pointer-events-auto flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/95 px-4 py-3 text-xs text-slate-200 shadow-2xl backdrop-blur';
    toast.innerHTML = `<i class="fa-solid ${icon}"></i><span></span>`;
    toast.querySelector('span').textContent = String(message || '');
    container.appendChild(toast);

    window.setTimeout(() => {
        toast.classList.add('opacity-0');
        toast.style.transition = 'opacity 180ms ease';
        window.setTimeout(() => toast.remove(), 220);
    }, 3200);
}

function updateEmptyState() {
    const grid = document.getElementById('movie-grid');
    const empty = document.getElementById('empty-state');
    if (!grid || !empty) return;

    const hasMovies = Array.isArray(moviesData) && moviesData.length > 0;
    empty.classList.toggle('hidden', hasMovies);
    grid.classList.toggle('hidden', !hasMovies);
}

function getNoCoverImage() {
    const svg = `
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 600">
            <rect width="400" height="600" fill="#020617"/>
            <rect x="90" y="150" width="220" height="250" rx="16" fill="#1e293b"/>
            <path d="M120 350l55-65 45 50 35-40 45 55" fill="none" stroke="#475569" stroke-width="12" stroke-linecap="round" stroke-linejoin="round"/>
            <circle cx="175" cy="225" r="22" fill="#475569"/>
            <text x="200" y="455" text-anchor="middle" fill="#64748b" font-size="24" font-family="Arial, sans-serif">No Poster</text>
        </svg>
    `;
    return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
}

function renderGrid() {
    const grid = document.getElementById('movie-grid');
    grid.innerHTML = '';
    const noCoverImage = getNoCoverImage();

    moviesData.forEach((movie, idx) => {
        const coverUrl = movie.cover ? '/api/proxy-img?url=' + encodeURIComponent(movie.cover) : noCoverImage;
        const card = document.createElement('article');
        card.className = 'movie-card group relative overflow-hidden rounded-2xl border border-slate-800/80 bg-slate-900/80 shadow-lg transition duration-300 hover:-translate-y-1 hover:border-slate-600 hover:shadow-2xl';
        card.onclick = (e) => { if (!e.target.closest('a, input, button')) openMovieDetail(idx); };
        card.innerHTML = `
            <div class="relative aspect-[2/3] overflow-hidden bg-slate-950">
                <img src="${coverUrl}" alt="${escapeHtml(movie.title)}" class="movie-cover h-full w-full object-cover transition duration-500 group-hover:scale-105" loading="lazy">
                <div class="absolute inset-0 bg-gradient-to-t from-slate-950 via-transparent to-black/10"></div>
                <div class="absolute right-2.5 top-2.5 rounded-lg border border-amber-300/20 bg-black/55 px-2 py-1 text-[11px] font-bold text-amber-300 backdrop-blur-md">
                    <i class="fa-solid fa-star mr-0.5"></i>${escapeHtml(movie.rate || '暂无')}
                </div>
                <div class="absolute bottom-2.5 left-2.5 right-2.5 flex items-center justify-between gap-2">
                    <div class="flex min-w-0 items-center gap-1.5">
                        <span class="shrink-0 whitespace-nowrap rounded-md border border-slate-400/20 bg-black/55 px-2 py-1 text-[10px] text-slate-300 backdrop-blur-md">${escapeHtml(movie.tag || currentTag)}</span>
                    </div>
                    <a href="${escapeHtml(movie.url || '#')}" target="_blank" rel="noopener noreferrer" class="douban-poster-btn shrink-0" aria-label="打开${escapeHtml(movie.provider_name || '影视详情')}">${escapeHtml(movie.provider_name || '详情')} <i class="fa-solid fa-arrow-up-right-from-square"></i></a>
                </div>
            </div>
            <div class="p-3">
                <div class="truncate text-sm font-semibold text-slate-100 group-hover:text-blue-300" title="${escapeHtml(movie.title)}">${escapeHtml(movie.title)}</div>
            </div>`;
        const cover = card.querySelector('.movie-cover');
        cover?.addEventListener('error', () => { if (cover.dataset.fallback === '1') return; cover.dataset.fallback = '1'; cover.src = noCoverImage; });
        grid.appendChild(card);
    });
}

let activeMovieDetailIndex = null;

function openMovieDetail(idx) {
    const movie = moviesData[idx];
    if (!movie) return;
    activeMovieDetailIndex = idx;
    const noCoverImage = getNoCoverImage();
    const cover = document.getElementById('movie-detail-cover');
    cover.src = movie.cover ? '/api/proxy-img?url=' + encodeURIComponent(movie.cover) : noCoverImage;
    cover.onerror = () => { cover.onerror = null; cover.src = noCoverImage; };
    document.getElementById('movie-detail-title').textContent = movie.title || '影视详情';
    document.getElementById('movie-detail-name').textContent = movie.title || '未命名影视';
    const metaParts = [
        '<div><span class="text-slate-500">分类：</span>' + escapeHtml(movie.tag || currentTag) + '</div>',
        movie.year ? '<div><span class="text-slate-500">年份：</span>' + escapeHtml(movie.year) + '</div>' : '',
        '<div><span class="text-slate-500">评分：</span>' + escapeHtml(movie.rate || '暂无') + '</div>'
    ].filter(Boolean);
    document.getElementById('movie-detail-meta').innerHTML = metaParts.join('') +
        '<div><span class="text-slate-500">操作：</span>选择资源版本转存，或为剧集开启自动追剧</div>';
    const summary = document.getElementById('movie-detail-summary');
    const summaryText = String(movie.summary || '').trim();
    summary.textContent = summaryText;
    summary.classList.toggle('hidden', !summaryText);
    const genres = document.getElementById('movie-detail-genres');
    const genreList = Array.isArray(movie.genres) ? movie.genres : [];
    genres.innerHTML = genreList.slice(0, 8).map(item =>
        '<span class="rounded-md border border-slate-700/60 bg-slate-950/45 px-2 py-1 text-[10px] text-slate-500">' +
        escapeHtml(item) + '</span>'
    ).join('');
    genres.classList.toggle('hidden', genreList.length === 0);
    const douban = document.getElementById('movie-detail-douban');
    douban.href = movie.url || '#';
    document.getElementById('movie-detail-drawer').classList.remove('hidden');
}

function closeMovieDetail() {
    document.getElementById('movie-detail-drawer')?.classList.add('hidden');
    activeMovieDetailIndex = null;
}


function movieDetailWatch() {
    const movie = Array.isArray(moviesData) && Number.isInteger(activeMovieDetailIndex)
        ? moviesData[activeMovieDetailIndex]
        : null;
    if (!movie) return;

    const category = String(movie.tag || currentTag || '电影').trim();
    const params = new URLSearchParams({
        title: movie.title || '',
        year: movie.year || movie.release_year || '',
        type: category
    });

    closeMovieDetail();
    window.location.assign('/playback?' + params.toString());
}

function movieDetailTransfer() {
    const movie = moviesData[activeMovieDetailIndex];
    if (!movie) return;
    const selectedMovie = {
        title: movie.title,
        tag: movie.tag || currentTag || '电影',
        media_type: movie.media_type || '',
        genres: Array.isArray(movie.genres) ? movie.genres : [],
        original_title: movie.original_title || '',
        cover: movie.cover,
        url: movie.url,
        rate: movie.rate || ''
    };
    try {
        sessionStorage.setItem('moviesync_resource_movie', JSON.stringify(selectedMovie));
    } catch (err) {
        console.warn('保存当前影视信息失败:', err);
        showToast('无法打开资源选择页，请刷新后重试', 'error');
        return;
    }
    closeMovieDetail();
    window.location.href = '/resource-select';
}

function movieDetailChase() {
    const movie = moviesData[activeMovieDetailIndex];
    if (!movie) return;
    closeMovieDetail();
    const params = new URLSearchParams({
        title: movie.title || '',
        tag: movie.tag || currentTag || '电视剧',
        cover: movie.cover || ''
    });
    window.location.href = '/tasks?' + params.toString();
}

function closeMoreDownloadLinks() {
    document.getElementById('more-download-links-modal')?.classList.add('hidden');
    document.getElementById('more-download-links-modal')?.classList.remove('flex');
}

async function movieDetailMoreLinks() {
    const movie = moviesData[activeMovieDetailIndex];
    if (!movie) return;
    const modal = document.getElementById('more-download-links-modal');
    const content = document.getElementById('more-links-content');
    const titleEl = document.getElementById('more-links-title');
    if (!modal || !content) return;
    titleEl.textContent = `更多下载链接 · ${movie.title || '影片'}`;
    content.innerHTML = '<div class="py-8 text-center text-sm text-slate-400"><i class="fa-solid fa-spinner fa-spin mr-2"></i>正在搜索 PanSou 的网盘与磁链资源…</div>';
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    try {
        const response = await apiFetch('/api/more-download-links', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ movie: { title: movie.title || '', tag: movie.tag || currentTag || '' } })
        });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || '搜索更多链接失败');
        const resources = Array.isArray(result.resources) ? result.resources : [];
        if (!resources.length) {
            content.innerHTML = '<div class="rounded-xl border border-slate-800 bg-slate-950/60 p-6 text-center text-sm text-slate-400">暂时没有找到更多类型的资源。可以稍后重试，或检查 PanSou 卡片状态。</div>';
            window.moreDownloadLinkResults = [];
            return;
        }
        window.moreDownloadLinkResults = resources;
        window.moreDownloadLinkFilter = 'all';
        window.moreDownloadLinkMovieTitle = movie.title || '影片';
        renderMoreDownloadLinks('all');
    } catch (err) {
        content.innerHTML = `<div class="rounded-xl border border-rose-900/50 bg-rose-950/20 p-5 text-sm text-rose-200">${escapeHtml(err.message || '搜索失败，请稍后重试')}</div>`;
    }
}

function moreDownloadLinkLabels() {
    return {
        quark_share: '夸克网盘', aliyun_share: '阿里云盘', baidu_share: '百度网盘',
        uc_share: 'UC 网盘', tianyi_share: '天翼云盘', mobile_share: '移动云盘',
        '115_share': '115 网盘', pikpak_share: 'PikPak', xunlei_share: '迅雷云盘',
        '123_share': '123 云盘', magnet: '磁力链接', ed2k: 'ED2K 链接'
    };
}

function filterMoreDownloadLinks(resourceType) {
    window.moreDownloadLinkFilter = resourceType || 'all';
    renderMoreDownloadLinks(window.moreDownloadLinkFilter);
}

function renderMoreDownloadLinks(selectedType = 'all') {
    const content = document.getElementById('more-links-content');
    const resources = Array.isArray(window.moreDownloadLinkResults) ? window.moreDownloadLinkResults : [];
    if (!content) return;
    const labels = moreDownloadLinkLabels();
    const counts = new Map();
    resources.forEach(item => {
        const type = String(item.resource_type || 'other');
        counts.set(type, (counts.get(type) || 0) + 1);
    });
    const categories = [
        { type: 'all', label: '全部', count: resources.length },
        ...Array.from(counts.keys()).sort((a, b) => (labels[a] || a).localeCompare(labels[b] || b, 'zh-CN'))
            .map(type => ({ type, label: labels[type] || type || '其他资源', count: counts.get(type) }))
    ];
    const tabs = categories.map(category => {
        const active = selectedType === category.type;
        return `<button type="button" onclick="filterMoreDownloadLinks('${escapeHtml(category.type)}')" class="shrink-0 rounded-lg border px-3 py-2 text-xs transition ${active ? 'border-amber-500/60 bg-amber-500/15 text-amber-200' : 'border-slate-700 bg-slate-950/50 text-slate-300 hover:border-slate-500 hover:text-white'}">${escapeHtml(category.label)} <span class="ml-1 opacity-70">${category.count}</span></button>`;
    }).join('');
    const visible = resources.map((item, index) => ({ item, index }))
        .filter(({ item }) => selectedType === 'all' || String(item.resource_type || 'other') === selectedType);
    const cards = visible.map(({ item, index }) => {
        const url = String(item.url || '');
        const label = labels[item.resource_type] || item.resource_type || '其他资源';
        const safeUrl = escapeHtml(url);
        const safeTitle = escapeHtml(item.title || window.moreDownloadLinkMovieTitle || '未命名资源');
        const safeChannel = escapeHtml(item.channel || item.source_name || 'PanSou');
        const safePassword = escapeHtml(item.password || '');
        const openable = /^https?:\/\//i.test(url);
        return `<article class="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
            <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0 flex-1">
                    <div class="mb-1 flex flex-wrap items-center gap-2"><span class="rounded-md bg-amber-500/10 px-2 py-1 text-[10px] text-amber-200">${escapeHtml(label)}</span><span class="text-[10px] text-slate-500">${safeChannel}</span></div>
                    <h4 class="break-words text-sm font-medium text-slate-100">${safeTitle}</h4>
                    <p class="mt-2 break-all text-xs leading-5 text-slate-400">${safeUrl}</p>
                    ${safePassword ? `<p class="mt-1 text-xs text-sky-300">提取码：${safePassword}</p>` : ''}
                </div>
                <div class="flex shrink-0 gap-2">
                    <button onclick="copyMoreDownloadLink(${index})" class="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800"><i class="fa-regular fa-copy mr-1"></i>复制</button>
                    ${openable ? `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="rounded-lg bg-blue-600/80 px-3 py-2 text-xs text-white hover:bg-blue-500">打开</a>` : ''}
                </div>
            </div>
        </article>`;
    }).join('');
    content.innerHTML = `<div class="mb-4"><div class="mb-2 text-xs text-slate-400">按资源类型筛选 · 共 ${resources.length} 条去重链接</div><div class="flex flex-wrap gap-2">${tabs}</div></div>${cards || '<div class="rounded-xl border border-slate-800 bg-slate-950/60 p-6 text-center text-sm text-slate-400">这个分类暂时没有资源。</div>'}`;
}

async function copyMoreDownloadLink(index) {
    const item = window.moreDownloadLinkResults?.[index];
    if (!item?.url) return;
    const text = item.password ? `${item.url}\n提取码：${item.password}` : item.url;
    try {
        await navigator.clipboard.writeText(text);
        showToast('链接已复制', 'success');
    } catch (err) {
        window.prompt('请复制以下链接：', text);
    }
}

async function searchAndOpenCandidates(selectedMovies, requestedTargetId = null, keepModalOpen = false) {
    const requestId = ++candidateSearchRequestSeq;
    const storageTargetId = String(
        requestedTargetId !== null
            ? requestedTargetId
            : (document.getElementById('batch-storage-target')?.value || window.defaultStorageTargetId || '')
    ).trim();
    if (!storageTargetId) {
        showToast('请先选择已启用的存储卡片，再搜索对应网盘资源', 'error');
        return;
    }

    const canPage = selectedMovies.length === 1;
    const title = selectedMovies[0]?.title || '';
    let candidatesMap = {};
    let opened = false;
    candidateSearchProgressText = canPage
        ? '正在启动资源检索：会并发查询已启用的资源来源，首批可用结果返回后立即显示。'
        : '';

    if (canPage && !keepModalOpen) {
        openCandidateModal(selectedMovies, {});
        opened = true;
        startCandidateSearchProgress(requestId);
    } else if (keepModalOpen && canPage) {
        startCandidateSearchProgress(requestId);
    }
    if (keepModalOpen) {
        candidateModalState = { movies: selectedMovies, candidatesMap: {} };
        const container = document.getElementById('candidate-content');
        if (container) {
            container.innerHTML = '<div id="candidate-search-progress" class="rounded-xl border border-sky-900/50 bg-sky-950/20 p-5 text-center text-xs leading-6 text-sky-300">正在按所选存储卡片重新搜索资源…</div>';
        }
    } else if (!canPage) {
        showToast('正在搜索资源，找到结果后会立即显示…', 'info');
    }

    const requestPage = async (offset = 0, sourceIds = null) => {
        const body = { movies: selectedMovies, storage_target_id: storageTargetId };
        if (canPage) {
            body.scan_limit = 10;
            body.offset = offset;
        }
        if (Array.isArray(sourceIds)) body.source_ids = sourceIds;
        const response = await apiFetch('/api/search-candidates', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const contentType = response.headers.get('content-type') || '';
        if (!response.ok || !contentType.toLowerCase().includes('application/json')) {
            throw new Error('搜索资源时服务没有正常响应，请稍后再试。如果一直失败，请查看后台日志。');
        }
        const result = await response.json();
        if (!result.success) throw new Error(result.message || '无法搜索资源');
        return result;
    };

    const mergePage = (pageMap) => {
        for (const movie of selectedMovies) {
            const movieTitle = movie.title;
            const incoming = Array.isArray(pageMap?.[movieTitle]) ? pageMap[movieTitle] : [];
            if (!Array.isArray(candidatesMap[movieTitle])) candidatesMap[movieTitle] = [];
            const seen = new Set(candidatesMap[movieTitle].map(item =>
                [item.source_id || '', item.resource_type || '', item.resource_id || item.share_id || ''].join('::')
            ));
            for (const item of incoming) {
                const key = [item.source_id || '', item.resource_type || '', item.resource_id || item.share_id || ''].join('::');
                if (!seen.has(key)) {
                    candidatesMap[movieTitle].push(item);
                    seen.add(key);
                }
            }
        }
    };

    const refreshModalPreservingSelection = () => {
        const container = document.getElementById('candidate-content');
        const scrollTop = container?.scrollTop || 0;
        const selected = new Set(
            [...(container?.querySelectorAll('input[type="checkbox"]:checked') || [])]
                .map(input => input.name + '::' + input.value)
        );
        openCandidateModal(selectedMovies, candidatesMap);
        opened = true;
        const refreshed = document.getElementById('candidate-content');
        if (!refreshed) return;
        for (const input of refreshed.querySelectorAll('input[type="checkbox"]')) {
            input.checked = selected.has(input.name + '::' + input.value);
            const match = input.name.match(/^batch-file-(\d+)-(\d+)$/);
            if (input.checked && match) updateCandidateSelectionCount(Number(match[1]), Number(match[2]));
        }
        refreshed.scrollTop = scrollTop;
    };

    try {
        if (canPage) {
            // Split the first scan by resource card. A slow card (for example a source
            // aggregating dozens of upstream providers) no longer blocks fast cards.
            let sourceResponse;
            try {
                const response = await apiFetch('/api/resource-sources');
                sourceResponse = await response.json();
                if (!response.ok || !sourceResponse.success) throw new Error('无法读取资源来源列表');
            } catch (err) {
                console.warn('读取资源来源列表失败，回退到兼容搜索模式:', err);
            }

            const sources = Array.isArray(sourceResponse?.resource_sources)
                ? sourceResponse.resource_sources.filter(source => source.enabled !== false && source.id)
                : [];

            if (!sources.length) {
                const result = await requestPage(0);
                if (requestId !== candidateSearchRequestSeq) return;
                stopCandidateSearchProgress(requestId);
                mergePage(result.candidates_map || {});
                const pageInfo = result.candidate_pages?.[title] || {};
                candidateSearchProgressText = pageInfo.has_more
                    ? `已找到 ${(candidatesMap[title] || []).length} 个资源，正在继续搜索…`
                    : `搜索完成，共找到 ${(candidatesMap[title] || []).length} 个可用资源。`;
                refreshModalPreservingSelection();
            } else {
                let nextSourceIndex = 0;
                let completedSources = 0;
                let failedSources = 0;
                let firstSourceResponse = false;
                const sourceNames = new Map(sources.map(source => [source.id, source.name || source.id]));
                const sourceFailures = [];

                const updateSourceProgress = (activeName = '') => {
                    const count = (candidatesMap[title] || []).length;
                    const remaining = Math.max(0, sources.length - completedSources);
                    const activeText = activeName ? `当前正在查询：${activeName}。` : '';
                    candidateSearchProgressText = remaining
                        ? `已完成 ${completedSources}/${sources.length} 个资源来源，发现 ${count} 个可用资源；还有 ${remaining} 个来源正在检索。 ${activeText}结果会陆续显示。`
                        : `来源检索完成，共找到 ${count} 个可用资源。${failedSources ? `另有 ${failedSources} 个来源请求失败。` : ''}`;
                };

                const searchOneSource = async source => {
                    let offset = 0;
                    let hasMore = true;
                    let sourceHadResponse = false;
                    try {
                        while (hasMore && requestId === candidateSearchRequestSeq) {
                            const result = await requestPage(offset, [source.id]);
                            if (requestId !== candidateSearchRequestSeq) return;
                            if (!firstSourceResponse) {
                                firstSourceResponse = true;
                                stopCandidateSearchProgress(requestId);
                            }
                            sourceHadResponse = true;
                            const before = (candidatesMap[title] || []).length;
                            mergePage(result.candidates_map || {});
                            const pageInfo = result.candidate_pages?.[title] || {};
                            hasMore = Boolean(pageInfo.has_more);
                            const nextOffset = Number(pageInfo.next_offset || 0);
                            const after = (candidatesMap[title] || []).length;
                            updateSourceProgress(sourceNames.get(source.id));
                            if (after > before || !opened) refreshModalPreservingSelection();
                            offset = nextOffset;
                            if (hasMore && offset <= 0) {
                                throw new Error('资源分页没有向前推进');
                            }
                        }

                        // PanSou's first-result endpoint returns an early cache snapshot
                        // while its background plugin batch continues. Re-read that card
                        // briefly so later plugin results are appended without restarting
                        // the entire movie search or disturbing the user's selection.
                        if (source.id === 'pansou' && sourceHadResponse) {
                            const pollingStartedAt = Date.now();
                            let lastGrowthAt = Date.now();
                            while (
                                requestId === candidateSearchRequestSeq
                                && Date.now() - pollingStartedAt < 12000
                                && (
                                    Date.now() - pollingStartedAt < 6500
                                    || Date.now() - lastGrowthAt < 2500
                                )
                            ) {
                                await new Promise(resolve => window.setTimeout(resolve, 700));
                                let pollOffset = 0;
                                let pollHasMore = true;
                                while (pollHasMore && requestId === candidateSearchRequestSeq) {
                                    const result = await requestPage(pollOffset, [source.id]);
                                    if (requestId !== candidateSearchRequestSeq) return;
                                    const before = (candidatesMap[title] || []).length;
                                    mergePage(result.candidates_map || {});
                                    const pageInfo = result.candidate_pages?.[title] || {};
                                    pollHasMore = Boolean(pageInfo.has_more);
                                    const nextOffset = Number(pageInfo.next_offset || 0);
                                    const after = (candidatesMap[title] || []).length;
                                    if (after > before) {
                                        lastGrowthAt = Date.now();
                                        updateSourceProgress(sourceNames.get(source.id));
                                        refreshModalPreservingSelection();
                                    }
                                    if (pollHasMore && nextOffset <= pollOffset) {
                                        throw new Error('PanSou 增量分页没有向前推进');
                                    }
                                    pollOffset = nextOffset;
                                }
                            }
                        }
                    } catch (err) {
                        failedSources++;
                        sourceFailures.push(`${sourceNames.get(source.id)}：${err.message || '请求失败'}`);
                    } finally {
                        completedSources++;
                        if (!sourceHadResponse && !firstSourceResponse) {
                            stopCandidateSearchProgress(requestId);
                            firstSourceResponse = true;
                        }
                        updateSourceProgress();
                        refreshModalPreservingSelection();
                    }
                };

                const worker = async () => {
                    while (requestId === candidateSearchRequestSeq) {
                        const index = nextSourceIndex++;
                        if (index >= sources.length) return;
                        await searchOneSource(sources[index]);
                    }
                };
                await Promise.all(Array.from(
                    { length: Math.min(4, sources.length) },
                    () => worker()
                ));
                if (requestId !== candidateSearchRequestSeq) return;
                candidateSearchProgressText = `搜索完成：查询 ${completedSources}/${sources.length} 个资源来源，共找到 ${(candidatesMap[title] || []).length} 个可用资源。${failedSources ? ` ${failedSources} 个来源失败。` : ''}`;
                refreshModalPreservingSelection();
                if (sourceFailures.length) console.debug('部分资源来源搜索失败:', sourceFailures);
                if (!(candidatesMap[title] || []).length) {
                    showToast(failedSources === sources.length
                        ? '资源来源请求均未成功，请检查卡片状态或后台日志。'
                        : '已搜索完当前启用的资源来源，暂未找到可用资源。', 'warning');
                } else {
                    showToast(`已陆续找到 ${candidatesMap[title].length} 个可用资源${failedSources ? `，另有 ${failedSources} 个来源失败` : ''}`, 'success');
                }
            }
            return;
        }

        // 多部影片仍使用旧的完整响应路径，避免改变批量搜索行为。
        const result = await requestPage(0);
        if (requestId !== candidateSearchRequestSeq) return;
        candidatesMap = result.candidates_map || {};
        const totalFound = selectedMovies.reduce((sum, movie) => sum + ((candidatesMap[movie.title] || []).length), 0);
        if (!totalFound) {
            const unavailable = (result.resource_sources || [])
                .filter(source => source.enabled !== false && source.health?.status === 'unavailable')
                .map(source => source.name || source.id)
                .join('、');
            showToast(result.message || (unavailable
                ? '资源来源不可用：' + unavailable + '。请先检查资源来源状态。'
                : '当前资源来源暂未找到可用候选，可稍后重试。'), 'warning');
            return;
        }
        candidateSearchProgressText = '';
        openCandidateModal(selectedMovies, candidatesMap);
        showToast('已按所选存储卡片找到 ' + totalFound + ' 个可用资源版本', 'success');
    } catch (err) {
        if (requestId !== candidateSearchRequestSeq) return;
        candidateSearchProgressText = opened
            ? '搜索遇到问题，已显示的资源仍可正常选择；可以稍后重新搜索。'
            : '';
        stopCandidateSearchProgress(requestId);
        if (opened) refreshModalPreservingSelection();
        else if (keepModalOpen) openCandidateModal(selectedMovies, {});
        showToast(err.message || '资源检索异常', 'error');
    } finally {
        stopCandidateSearchProgress(requestId);
    }
}


function toggleLogBox(show = true) {
    const panel = document.getElementById('log-panel');
    if (show) {
        panel.classList.remove('hidden'); // 不再强制覆盖不透明背景，保持 HTML 中的透明毛玻璃样式
    } else {
        panel.classList.add('hidden');
    }
}

function clearLog() {
    document.getElementById('log-box').innerHTML = '';
}

function startLogTimer() {
    logTimerSeconds = 0;
    if (logAutoCloseTimer) {
        clearInterval(logAutoCloseTimer);
        logAutoCloseTimer = null;
    }
    const badge = document.getElementById('log-timer-badge');
    badge.classList.remove('hidden');
    badge.innerText = `耗时: 0 秒`;
    if (logTimerInterval) clearInterval(logTimerInterval);
    logTimerInterval = setInterval(() => {
        logTimerSeconds++;
        badge.innerText = `耗时: ${logTimerSeconds} 秒`;
    }, 1000);
}

function stopLogTimer() {
    if (logTimerInterval) {
        clearInterval(logTimerInterval);
        logTimerInterval = null;
    }
}

function appendLogLine(text, type = 'info') {
    const logBox = document.getElementById('log-box');
    if (!text) return;
    const lines = text.split('\n');
    lines.forEach(line => {
        if (line.trim().length > 0) {
            const div = document.createElement('div');
            let colorClass = 'text-sky-400';
            if (type === 'success') colorClass = 'text-emerald-400 font-semibold';
            else if (type === 'warn') colorClass = 'text-amber-400';
            else if (type === 'error') colorClass = 'text-rose-400 font-bold';

            div.className = `py-0.5 font-mono leading-relaxed ${colorClass} border-b border-slate-900/40`;
            div.textContent = line;
            logBox.appendChild(div);
        }
    });
    logBox.scrollTop = logBox.scrollHeight;
}

function formatCandidateSize(bytes) {
    const n = Number(bytes || 0);
    if (!n) return '未知大小';
    const units = ['B','KB','MB','GB','TB'];
    let value = n, i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return (value >= 10 || i === 0 ? value.toFixed(0) : value.toFixed(1)) + ' ' + units[i];
}

function getCandidateVersionLabel(candidate, files, resolutionText) {
    const names = files.map(file => String(file.file_name || '').toLowerCase()).join(' ');
    const tags = [];
    if (resolutionText && resolutionText !== '未知') tags.push(resolutionText);
    if (/\b(web[- .]?dl|webdl|web rip)\b/.test(names)) tags.push('WEB-DL');
    else if (/\b(blu[- .]?ray|bluray|bdrip|bdremux|remux)\b/.test(names)) tags.push('BluRay');
    else if (/\b(hdtv)\b/.test(names)) tags.push('HDTV');
    if (/\b(h265|hevc|x265)\b/.test(names)) tags.push('H.265');
    else if (/\b(h264|avc|x264)\b/.test(names)) tags.push('H.264');
    if (!tags.length) tags.push(candidate.channel ? '资源版本' : '可用资源');
    return [...new Set(tags)].join(' · ');
}

function isEpisodeFile(fileName) {
    const text = String(fileName || '');
    return /(?:S\d{1,2}E\d{1,4}|第\s*\d+\s*[集话]|(?:EP|E)\s*\d{1,4})/i.test(text);
}

function updateCandidateSelectionCount(mIdx, cIdx) {
    const inputs = document.querySelectorAll(`input[name="batch-file-${mIdx}-${cIdx}"]`);
    const selected = Array.from(inputs).filter(input => input.checked).length;
    const count = document.getElementById(`candidate-selected-count-${mIdx}-${cIdx}`);
    const button = document.getElementById(`candidate-transfer-${mIdx}-${cIdx}`);
    if (count) count.textContent = `已选 ${selected} 个`;
    if (button) {
        button.disabled = selected === 0;
        button.classList.toggle('opacity-50', selected === 0);
        button.classList.toggle('cursor-not-allowed', selected === 0);
    }
}

function setCandidateFiles(mIdx, cIdx, checked) {
    document.querySelectorAll(`input[name="batch-file-${mIdx}-${cIdx}"]`).forEach(input => {
        input.checked = checked;
    });
    updateCandidateSelectionCount(mIdx, cIdx);
}

function toggleCandidateFiles(mIdx, cIdx) {
    const box = document.getElementById(`candidate-files-${mIdx}-${cIdx}`);
    const button = document.getElementById(`candidate-files-toggle-${mIdx}-${cIdx}`);
    if (!box || !button) return;
    const hidden = box.classList.toggle('hidden');
    button.innerHTML = hidden
        ? '<i class="fa-solid fa-chevron-down mr-1"></i>查看剧集文件'
        : '<i class="fa-solid fa-chevron-up mr-1"></i>收起剧集文件';
}

function openCandidateModal(movies, candidatesMap) {
    candidateModalState = { movies, candidatesMap };
    loadCategoryOptions();
    const container = document.getElementById('candidate-content');
    container.innerHTML = '';
    if (candidateSearchProgressText) {
        const progress = document.createElement('div');
        progress.id = 'candidate-search-progress';
        progress.className = 'rounded-xl border border-sky-900/50 bg-sky-950/20 px-4 py-3 text-xs text-sky-300';
        progress.textContent = candidateSearchProgressText;
        container.appendChild(progress);
    }

    movies.forEach((movie, mIdx) => {
        const title = movie.title;
        const candidates = candidatesMap[title] || [];
        const movieSection = document.createElement('section');
        movieSection.className = 'candidate-movie-section rounded-2xl border border-slate-800 bg-slate-950/70 p-4 space-y-4';
        let html = `
            <div class="flex flex-wrap items-end justify-between gap-2 border-b border-slate-800 pb-3">
                <div>
                    <div class="text-sm font-semibold text-white">🎬 《${escapeHtml(title)}》</div>
                    <div class="mt-1 text-[11px] text-slate-500">找到 ${candidates.length} 个可选资源版本 · 默认不选择</div>
                </div>
            </div>`;

        if (!candidates.length) {
            const isStillSearching = candidateSearchProgressText
                && !candidateSearchProgressText.includes('搜索完成')
                && !candidateSearchProgressText.includes('失败');
            html += isStillSearching
                ? '<div class="candidate-search-empty-state rounded-xl border border-sky-900/50 bg-sky-950/20 p-4 text-xs leading-6 text-sky-300"></div>'
                : '<div class="rounded-xl border border-amber-900/50 bg-amber-950/20 p-4 text-xs text-amber-300">未找到可用资源，或后续检索未能完成。</div>';
        } else {
            html += '<div class="space-y-3">';
            candidates.forEach((cand, cIdx) => {
                const files = Array.isArray(cand.files) ? cand.files : [];
                const resolutions = Object.keys(cand.resolutions || {});
                const resolutionText = resolutions.length
                    ? resolutions.join(' / ')
                    : [...new Set(files.map(f => f.resolution).filter(Boolean))].join(' / ') || '未知';
                const totalSize = cand.total_size_text || formatCandidateSize(
                    files.reduce((sum, f) => sum + Number(f.size || 0), 0)
                );
                const versionLabel = getCandidateVersionLabel(cand, files, resolutionText);
                const episodeCount = files.filter(file => isEpisodeFile(file.file_name)).length;
                const seriesCategory = ['电视剧', '综艺', '动漫', '纪录片', '短剧'].includes(movie.tag);
                const isSeries = (movie.media_type === 'tv' || seriesCategory) && (episodeCount >= 1 || files.length >= 2);
                const filesBoxId = `candidate-files-${mIdx}-${cIdx}`;
                const toggleId = `candidate-files-toggle-${mIdx}-${cIdx}`;
                const fileCheckboxes = files.map((f, fIdx) => `
                    <label class="candidate-file-row flex cursor-pointer items-center gap-3 rounded-lg border border-transparent px-3 py-2 hover:border-slate-700 hover:bg-slate-800/70">
                        <input type="checkbox" name="batch-file-${mIdx}-${cIdx}" value="${escapeHtml(f.fid)}" class="h-4 w-4 shrink-0 rounded border-slate-600 bg-slate-900 text-blue-600 focus:ring-0" onchange="updateCandidateSelectionCount(${mIdx}, ${cIdx})">
                        <span class="min-w-0 flex-1 truncate text-xs text-slate-300" title="${escapeHtml(f.file_name)}">${escapeHtml(f.file_name)}</span>
                        <span class="shrink-0 text-[10px] text-slate-500">${escapeHtml(f.resolution || '未知')} · ${escapeHtml(f.size_text || formatCandidateSize(f.size))}</span>
                    </label>`).join('');

                html += `
                    <article class="candidate-resource-card rounded-2xl border border-slate-800 bg-slate-900/80 p-4 transition hover:border-slate-700">
                        <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                            <div class="min-w-0">
                                <div class="flex flex-wrap items-center gap-2">
                                    <span class="text-sm font-semibold text-slate-100">
                                        <i class="fa-solid fa-layer-group mr-1.5 text-blue-400"></i>${escapeHtml(versionLabel)}
                                    </span>
                                    <span class="rounded-lg border border-slate-700/70 bg-slate-950/60 px-2 py-1 text-[10px] text-slate-500">
                                        ${isSeries ? '剧集资源' : '电影资源'}
                                    </span>
                                </div>
                                <div class="mt-3 flex flex-wrap gap-2">
                                    ${cand.resource_title ? `<span class="resource-chip max-w-full truncate" title="${escapeHtml(cand.resource_title)}">来源标题：${escapeHtml(cand.resource_title)}</span>` : ''}
                                    ${cand.title_mismatch ? '<span class="resource-chip text-amber-300">标题疑似不匹配：未找到续作编号</span>' : ''}
                                    ${cand.format_mismatch ? '<span class="resource-chip text-amber-300">疑似剧集文件，与所选电影类型不符</span>' : ''}
                                    <span class="resource-chip"><i class="fa-solid fa-film"></i> ${files.length} 个视频</span>
                                    <span class="resource-chip"><i class="fa-solid fa-hard-drive"></i> ${escapeHtml(totalSize)}</span>
                                    <span class="resource-chip resource-chip-accent"><i class="fa-solid fa-display"></i> ${escapeHtml(resolutionText)}</span>
                                    ${isSeries ? '<span class="resource-chip"><i class="fa-solid fa-list-ol"></i> ' + episodeCount + ' 集可选</span>' : ''}
                                </div>
                            </div>
                            <button id="candidate-transfer-${mIdx}-${cIdx}" disabled onclick="confirmBatchTransferForCandidate(${mIdx}, ${cIdx})" class="shrink-0 rounded-xl bg-emerald-600 px-4 py-2.5 text-xs font-semibold text-white shadow-lg shadow-emerald-600/15 hover:bg-emerald-500 opacity-50 cursor-not-allowed">
                                <i class="fa-solid fa-cloud-arrow-down mr-1"></i> 转存所选 <span id="candidate-selected-count-${mIdx}-${cIdx}" class="ml-1 text-[10px] font-normal opacity-80">已选 0 个</span>
                            </button>
                        </div>
                        <div class="mt-4 overflow-hidden rounded-xl border border-slate-800 bg-slate-950/80">
                            <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-3 py-2">
                                <span class="text-[11px] font-medium text-slate-300">
                                    ${isSeries ? '剧集文件 · 默认不选择' : '文件列表 · 默认不选择'}
                                </span>
                                <div class="flex items-center gap-3">
                                    <button type="button" onclick="setCandidateFiles(${mIdx}, ${cIdx}, true)" class="text-[10px] text-blue-400 hover:text-blue-300">全选</button>
                                    <button type="button" onclick="setCandidateFiles(${mIdx}, ${cIdx}, false)" class="text-[10px] text-slate-500 hover:text-slate-300">清空</button>
                                    ${isSeries ? `<button id="${toggleId}" type="button" onclick="toggleCandidateFiles(${mIdx}, ${cIdx})" class="text-[10px] text-slate-400 hover:text-white"><i class="fa-solid fa-chevron-down mr-1"></i>查看剧集文件</button>` : ''}
                                </div>
                            </div>
                            <div id="${filesBoxId}" class="${isSeries ? 'hidden ' : ''}max-h-56 overflow-y-auto p-2">
                                ${fileCheckboxes || '<div class="p-4 text-center text-xs text-slate-500">没有可显示的文件</div>'}
                            </div>
                        </div>
                    </article>`;
            });
            html += '</div>';
        }

        movieSection.innerHTML = html;
        container.appendChild(movieSection);
    });
    document.getElementById('candidate-modal').classList.remove('hidden');
}

function closeCandidateModal() {
    document.getElementById('candidate-modal').classList.add('hidden');
}

function confirmBatchTransferForCandidate(mIdx, cIdx) {
    const movie = candidateModalState?.movies?.[mIdx];
    const title = movie?.title || "未知影片";
    const candidate = candidateModalState?.candidatesMap?.[title]?.[cIdx];
    if (!movie || !candidate) return alert('这条资源信息已失效，请重新搜索');
    const checkboxes = document.querySelectorAll(`input[name="batch-file-${mIdx}-${cIdx}"]:checked`);
    const selectedFids = Array.from(checkboxes).map(cb => cb.value);

    if (selectedFids.length === 0) {
        return alert('请至少选择一个要转存的文件！');
    }

    const filteredFiles = candidate.files.filter(f => selectedFids.includes(String(f.fid)));
    const customCandidate = { ...candidate, files: filteredFiles };
    const targetFid = document.getElementById('batch-target-fid').value;
    confirmTransferAndSave(movie, customCandidate, targetFid);
}

async function confirmTransferAndSave(movie, candidate, targetFid = '0') {
    const storageTargetId = String(document.getElementById('batch-storage-target')?.value || '');
    const storageTarget = (window.storageTargets || []).find(target => target.id === storageTargetId);
    if (!storageTargetId || !storageTarget) {
        showToast('请先选择可用的存储卡片', 'error');
        return;
    }
    const resourceType = String(candidate?.resource_type || '').trim().toLowerCase();
    const capabilities = Array.isArray(storageTarget.capabilities) ? storageTarget.capabilities : [];
    if (resourceType && !capabilities.includes('storage.accepts.' + resourceType)
        && !capabilities.includes('storage.accepts.*')) {
        showToast('当前存储卡片不支持此资源类型。请在弹窗顶部切换到兼容的存储卡片，保存目录会随之更新。', 'error');
        return;
    }
    closeCandidateModal();
    try {
        const response = await apiFetch('/api/transfer-selected', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                movie,
                candidate: {...candidate, storage_target_id: storageTargetId},
                target_fid: targetFid,
                storage_target_id: storageTargetId
            })
        });
        const res = await response.json();
        if (!res.success) {
            showToast(res.message || '无法创建转存任务', 'error');
            return;
        }
        const taskId = res.task?.id;
        showTaskCreatedToast(movie?.title || '资源', taskId);
    } catch (err) {
        showToast('创建转存任务失败：' + (err.message || '未知错误'), 'error');
    }
}

function showTaskCreatedToast(title, taskId) {
    const box = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = 'pointer-events-auto rounded-xl border border-emerald-800/70 bg-slate-900/95 px-4 py-3 shadow-2xl';
    el.innerHTML =
        '<div class="flex items-start gap-3">' +
        '<div class="mt-0.5 text-emerald-400"><i class="fa-solid fa-circle-check"></i></div>' +
        '<div class="min-w-0 flex-1"><div class="text-xs font-semibold text-white">转存任务已创建</div>' +
        '<div class="mt-1 truncate text-[11px] text-slate-400">《' + escapeHtml(title) + '》已进入任务中心</div>' +
        '<div class="mt-2 flex gap-2">' +
        '<a href="/tasks" class="rounded-md bg-purple-600 px-2.5 py-1.5 text-[10px] font-medium text-white hover:bg-purple-500">查看任务</a>' +
        '<button onclick="this.closest(\'div.pointer-events-auto\')?.remove()" class="rounded-md border border-slate-700 px-2.5 py-1.5 text-[10px] text-slate-400">继续浏览</button>' +
        '</div></div></div>';
    box.appendChild(el);
    setTimeout(() => el.remove(), 8000);
}



