/* Player-first MacCMS movie playback. Keeps transfer/chase flows untouched. */
(() => {
    let modal;
    let inlineMode = false;
    let video;
    let playerMessage;
    let activeHls = null;
    let searchRequest = 0;
    let detailRequest = 0;
    let selectedCandidate = null;
    let currentDetail = null;
    let currentSourceName = '';
    let activeLineIndex = 0;
    let autoPlayCandidates = [];
    let autoPlayCandidateIndex = 0;
    let autoPlayUrls = [];
    let autoPlayUrlIndex = 0;
    let autoPlayActive = false;
    let autoPlayGeneration = 0;
    let mediaType = 'movie';
    let autoPlayEpisodeMode = false;

    const $ = (selector) => modal.querySelector(selector);

    function ensureModal() {
        if (modal) return modal;
        const inlineRoot = document.getElementById('maccms-inline-panel');
        inlineMode = Boolean(inlineRoot);
        modal = inlineRoot || document.createElement('div');
        modal.id = inlineMode ? 'maccms-inline-panel' : 'maccms-watch-modal';
        modal.className = inlineMode ? 'hidden mt-4 min-w-0 maccms-inline-layout' : 'fixed inset-0 z-[120] hidden items-center justify-center bg-black/80 p-2 backdrop-blur-sm sm:p-4';
        modal.innerHTML = `
            <section class="${inlineMode ? 'w-full overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-xl' : 'flex max-h-[96vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl'}">
                <header class="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3 sm:px-5">
                    <div class="min-w-0"><div class="text-[10px] uppercase tracking-widest text-emerald-400">MOVIESYNC · MACCMS</div><h3 id="maccms-watch-title" class="mt-1 truncate text-sm font-semibold text-white">立即观看</h3></div>
                    <button type="button" data-watch-close class="rounded-lg px-3 py-2 text-slate-400 hover:bg-slate-800 hover:text-white" aria-label="关闭"><i class="fa-solid fa-xmark"></i></button>
                </header>
                <form id="maccms-watch-search-form" class="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-900/50 px-4 py-3 sm:px-5">
                    <label class="flex min-w-[150px] flex-1 items-center gap-2 rounded-lg border border-slate-700 bg-slate-950 px-3">
                        <i class="fa-solid fa-magnifying-glass text-xs text-slate-500"></i>
                        <input id="maccms-watch-search-input" type="search" placeholder="搜索影片或剧集名称…" class="w-full min-w-0 bg-transparent py-2 text-xs text-slate-200 outline-none placeholder:text-slate-600" required>
                    </label>
                    <input id="maccms-watch-search-year" type="number" min="1900" max="2099" placeholder="年份（可选）" aria-label="影片年份（可选）" class="w-28 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none placeholder:text-slate-600">
                    <button type="submit" class="rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-emerald-500"><i class="fa-solid fa-search mr-1"></i>搜索</button>
                </form>
                <div id="maccms-watch-progress" class="hidden mx-3 mt-3 rounded-xl border border-emerald-900/50 bg-emerald-950/20 p-3 sm:mx-5 sm:p-4" role="status" aria-live="polite">
                    <div class="flex items-start gap-3">
                        <span id="maccms-watch-progress-icon" class="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-900/50 text-emerald-300"><i class="fa-solid fa-magnifying-glass"></i></span>
                        <div class="min-w-0 flex-1">
                            <div id="maccms-watch-progress-title" class="text-xs font-semibold text-emerald-100">准备在线点播</div>
                            <p id="maccms-watch-progress-detail" class="mt-1 break-words text-[11px] leading-5 text-slate-400">正在准备搜索…</p>
                        </div>
                        <span id="maccms-watch-progress-state" class="shrink-0 text-[10px] text-emerald-300">进行中</span>
                    </div>
                    <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-800">
                        <div id="maccms-watch-progress-bar" class="h-full w-[8%] rounded-full bg-emerald-400 transition-all duration-500"></div>
                    </div>
                    <div class="mt-3 grid grid-cols-4 gap-1.5 text-center text-[9px] sm:text-[10px]">
                        <div data-progress-step="0" class="rounded-md bg-slate-900/70 px-1.5 py-1.5 text-slate-500">影片信息</div>
                        <div data-progress-step="1" class="rounded-md bg-slate-900/70 px-1.5 py-1.5 text-slate-500">搜索资源站</div>
                        <div data-progress-step="2" class="rounded-md bg-slate-900/70 px-1.5 py-1.5 text-slate-500">匹配资源</div>
                        <div data-progress-step="3" class="rounded-md bg-slate-900/70 px-1.5 py-1.5 text-slate-500">尝试播放</div>
                    </div>
                </div>
                <div class="min-h-0 flex-1 overflow-y-auto">
                    <div class="grid gap-4 p-3 sm:p-5 lg:grid-cols-[minmax(0,1.65fr)_minmax(260px,1fr)]">
                        <div class="min-w-0">
                            <div id="maccms-watch-player-wrap" class="relative overflow-hidden rounded-xl border border-slate-800 bg-black">
                                <video id="maccms-watch-video" class="aspect-video w-full bg-black" controls playsinline preload="metadata"></video>
                                <div id="maccms-watch-placeholder" class="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-slate-900 text-center">
                                    <i class="fa-solid fa-circle-play text-4xl text-emerald-400"></i>
                                    <p class="px-6 text-sm text-slate-300">先选择一部匹配的影片，再点击播放</p>
                                </div>
                            </div>
                            <p id="maccms-watch-player-message" role="status" aria-live="polite" class="mt-3 text-xs leading-5 text-slate-400">正在搜索已启用的采集源…</p>
                            <div class="mt-4 rounded-xl border border-slate-800 bg-slate-900/60 p-3 sm:p-4">
                                <div class="flex flex-wrap items-center justify-between gap-2">
                                    <div><div class="text-[10px] uppercase tracking-wider text-slate-500">当前影片</div><h4 id="maccms-watch-selected-title" class="mt-1 text-sm font-semibold text-white">尚未选择影片</h4></div>
                                    <span id="maccms-watch-selected-meta" class="text-[10px] text-slate-400"></span>
                                </div>
                                <div id="maccms-watch-lines" class="mt-3 space-y-3"><p class="text-xs text-slate-500">匹配影片后，这里会显示线路。</p></div>
                            </div>
                        </div>
                        <aside class="min-w-0">
                            <div class="flex items-center justify-between gap-2">
                                <div><h4 class="text-sm font-semibold text-white">匹配影片</h4><p id="maccms-watch-status" class="mt-1 text-[11px] leading-5 text-slate-400">正在查询采集站点…</p></div>
                                <button type="button" id="maccms-watch-results-toggle" class="shrink-0 rounded-lg border border-slate-700 px-2.5 py-2 text-[11px] text-slate-300 hover:bg-slate-800">收起列表</button>
                            </div>
                            <div id="maccms-watch-results" class="mt-3 space-y-2"></div>
                            <div id="maccms-watch-errors" class="mt-3 space-y-2"></div>
                        </aside>
                    </div>
                </div>
                <footer class="border-t border-slate-800 px-4 py-2.5 text-[10px] leading-5 text-slate-500 sm:px-5">“优先尝试”仅依据片名、年份和线路信息排序，不代表线路已验证可播放。播放由你点击确认；失败时可直接切换线路。</footer>
            </section>`;
        if (!inlineMode) document.body.appendChild(modal);
        video = $('#maccms-watch-video');
        if (inlineMode) {
            $('#maccms-watch-player-wrap').classList.add('hidden');
            const closeButton = modal.querySelector('[data-watch-close]');
            if (closeButton) closeButton.innerHTML = '<i class="fa-solid fa-chevron-up mr-1"></i>收起点播';
        }
        $('#maccms-watch-search-form').addEventListener('submit', event => {
            event.preventDefault();
            searchTitle($('#maccms-watch-search-input').value, $('#maccms-watch-search-year').value);
        });
        playerMessage = $('#maccms-watch-player-message');
        $('#maccms-watch-placeholder').addEventListener('click', () => {
            const first = $('#maccms-watch-lines button[data-play-url]');
            if (first) first.focus();
        });
        modal.querySelector('[data-watch-close]').addEventListener('click', closeModal);
        modal.addEventListener('click', event => {
            if (event.target === modal) closeModal();
        });
        $('#maccms-watch-results-toggle').addEventListener('click', () => {
            const results = $('#maccms-watch-results');
            const hidden = results.classList.toggle('hidden');
            $('#maccms-watch-errors').classList.toggle('hidden', hidden);
            $('#maccms-watch-results-toggle').textContent = hidden ? '展开列表' : '收起列表';
        });
        return modal;
    }

    function setAutoProgress(stage, title, detail = '') {
        const panel = $('#maccms-watch-progress');
        if (!panel) return;
        const stages = { info: 0, search: 1, match: 2, play: 3, searchFailed: 1, matchFailed: 2, playFailed: 3, success: 4, failed: 3 };
        const index = stages[stage] ?? 0;
        const failed = stage.endsWith('Failed') || stage === 'failed';
        panel.classList.remove('hidden', 'border-rose-900/60', 'bg-rose-950/20');
        panel.classList.toggle('border-emerald-900/50', !failed);
        panel.classList.toggle('border-rose-900/60', failed);
        panel.classList.toggle('bg-emerald-950/20', !failed);
        panel.classList.toggle('bg-rose-950/20', failed);
        $('#maccms-watch-progress-title').textContent = title;
        $('#maccms-watch-progress-detail').textContent = detail;
        $('#maccms-watch-progress-state').textContent = stage === 'success' ? '已完成' : failed ? '未完成' : '进行中';
        $('#maccms-watch-progress-state').className = 'shrink-0 text-[10px] ' + (failed ? 'text-rose-300' : 'text-emerald-300');
        const bar = $('#maccms-watch-progress-bar');
        bar.style.width = (stage === 'success' ? 100 : index === 0 ? 12 : index === 1 ? 35 : index === 2 ? 58 : 82) + '%';
        bar.classList.toggle('bg-rose-400', failed);
        bar.classList.toggle('bg-emerald-400', !failed);
        bar.classList.toggle('animate-pulse', stage !== 'success' && !failed);
        $('#maccms-watch-progress-icon').innerHTML = stage === 'success'
            ? '<i class="fa-solid fa-circle-check"></i>'
            : failed
                ? '<i class="fa-solid fa-triangle-exclamation"></i>'
                : '<i class="fa-solid fa-circle-notch fa-spin"></i>';
        $('#maccms-watch-progress-icon').className = 'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ' +
            (failed ? 'bg-rose-900/50 text-rose-300' : 'bg-emerald-900/50 text-emerald-300');
        panel.querySelectorAll('[data-progress-step]').forEach(step => {
            const stepIndex = Number(step.dataset.progressStep);
            const complete = stage === 'success' || stepIndex < index;
            const active = stage !== 'success' && !failed && stepIndex === index;
            step.classList.toggle('text-emerald-200', complete || active);
            step.classList.toggle('border', active);
            step.classList.toggle('border-emerald-700/70', active);
            step.classList.toggle('text-rose-300', failed && stepIndex === index);
            step.classList.toggle('text-slate-500', !complete && !active && !(failed && stepIndex === index));
            if (complete) step.innerHTML = '<i class="fa-solid fa-check mr-1"></i>' + ['影片信息', '搜索资源站', '匹配资源', '尝试播放'][stepIndex];
            else step.textContent = ['影片信息', '搜索资源站', '匹配资源', '尝试播放'][stepIndex];
        });
    }

    function resetVideo() {
        if (activeHls) {
            activeHls.destroy();
            activeHls = null;
        }
        if (video) {
            video.pause();
            video.removeAttribute('src');
            video.load();
        }
    }

    function closeModal() {
        if (!modal) return;
        searchRequest++;
        detailRequest++;
        resetVideo();
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        if (inlineMode && window.MovieSyncPlayback && window.MovieSyncPlayback.showFiles) window.MovieSyncPlayback.showFiles();
    }

    function addText(parent, tag, className, value) {
        const node = document.createElement(tag);
        node.className = className;
        node.textContent = String(value || '');
        parent.appendChild(node);
        return node;
    }

    function openModal(title) {
        ensureModal();
        resetVideo();
        selectedCandidate = null;
        currentDetail = null;
        currentSourceName = '';
        activeLineIndex = 0;
        $('#maccms-watch-title').textContent = title || '立即观看';
        $('#maccms-watch-status').textContent = '正在查询启用的采集站点…';
        $('#maccms-watch-results').replaceChildren();
        $('#maccms-watch-errors').replaceChildren();
        $('#maccms-watch-errors').classList.remove('hidden');
        $('#maccms-watch-results').classList.remove('hidden');
        $('#maccms-watch-results-toggle').textContent = '收起列表';
        $('#maccms-watch-lines').innerHTML = '<p class="text-xs text-slate-500">选择匹配影片后，这里会显示线路。</p>';
        $('#maccms-watch-selected-title').textContent = '尚未选择影片';
        $('#maccms-watch-selected-meta').textContent = '';
        $('#maccms-watch-placeholder').classList.remove('hidden');
        playerMessage.textContent = '选择匹配影片，再手动点击一条线路的播放按钮。';
        $('#maccms-watch-progress').classList.add('hidden');
        modal.classList.remove('hidden');
        if (!inlineMode) modal.classList.add('flex');
    }

    async function fetchJson(url) {
        const response = typeof apiFetch === 'function' ? await apiFetch(url) : await fetch(url, { credentials: 'same-origin' });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || '请求失败');
        return result;
    }

    function resolveMediaType(value) {
        const category = String(value || '').trim().toLowerCase();
        if (/电视剧|连续剧|剧集|series|tv show/.test(category)) return 'series';
        if (/动漫|动画|番剧|anime|animation/.test(category)) return 'anime';
        if (/综艺|真人秀|variety|reality/.test(category)) return 'variety';
        return 'movie';
    }

    function isEpisodicMedia() { return mediaType !== 'movie'; }

    function episodeMatchKey(value) {
        const raw = String(value || '').trim();
        if (!raw) return null;
        let match = raw.match(/\bs(\d{1,2})\s*e(?:p)?\s*(\d{1,3})\b/i);
        if (match) return `s${Number(match[1])}e${Number(match[2])}`;
        match = raw.match(/第\s*0*(\d{1,4})\s*(?:集|话|話|期|回)/i);
        if (match) return `ep:${Number(match[1])}`;
        match = raw.match(/^\s*(?:ep|episode)?\s*0*(\d{1,4})\s*$/i);
        if (match) return `ep:${Number(match[1])}`;
        const normalized = normalizeTitle(raw);
        if (!normalized || /^(正片|播放|播放地址|未知|无标题|默认|main|play|video|episode)$/.test(normalized)) return null;
        return `name:${normalized}`;
    }

    function normalizeTitle(value) {
        return String(value || '').toLowerCase()
            .replace(/[：:!！?？、，,。.'’"“”()（）\[\]【】\s_-]+/g, '');
    }

    function candidateScore(item, targetTitle, targetYear) {
        const title = normalizeTitle(item.title);
        const target = normalizeTitle(targetTitle);
        let score = 0;
        if (title && title === target) score += 100;
        else if (title && target && (title.includes(target) || target.includes(title))) score += 45;
        else {
            const tokens = target.match(/[\p{L}\p{N}]+/gu) || [];
            const matched = tokens.filter(token => token.length > 1 && title.includes(token)).length;
            score += tokens.length ? Math.round(30 * matched / tokens.length) : 0;
        }
        const year = String(item.year || '').match(/\b(19\d{2}|20\d{2})\b/);
        const wantedYear = String(targetYear || '').match(/\b(19\d{2}|20\d{2})\b/);
        if (year && wantedYear) score += year[1] === wantedYear[1] ? 24 : -12;
        const category = String(item.category || item.type || '').toLowerCase();
        const expectedCategory = mediaType === 'series' ? /电视剧|连续剧|剧集|series|tv/i.test(category)
            : mediaType === 'anime' ? /动漫|动画|番剧|anime|animation/i.test(category)
                : mediaType === 'variety' ? /综艺|真人秀|variety|reality/i.test(category)
                    : /电影|movie/i.test(category);
        const clearlyMovie = /电影|movie/i.test(category);
        const clearlySeries = /电视剧|连续剧|剧集|series|tv/i.test(category);
        const clearlyAnime = /动漫|动画|番剧|anime|animation/i.test(category);
        const clearlyVariety = /综艺|真人秀|variety|reality/i.test(category);
        if (category && expectedCategory) score += 8;
        else if (category && (clearlyMovie || clearlySeries || clearlyAnime || clearlyVariety)) score -= 12;
        if (Array.isArray(item.play_sources) && item.play_sources.length) score += 3;
        if (item.remarks) score += 1;
        return score;
    }

    function candidateSubtitle(candidate) {
        const item = candidate.item;
        return [candidate.source.source_name, item.year, item.category, item.remarks].filter(Boolean).join(' · ');
    }

    function renderCandidates(results, targetTitle, targetYear) {
        const root = $('#maccms-watch-results');
        const errors = $('#maccms-watch-errors');
        root.replaceChildren();
        errors.replaceChildren();
        const candidates = [];
        for (const source of (results || [])) {
            if (source.error) {
                const row = document.createElement('div');
                row.className = 'rounded-lg border border-amber-900/40 bg-amber-950/20 p-2.5 text-[11px] leading-5 text-amber-200';
                row.textContent = (source.source_name || '采集源') + '：' + source.error;
                errors.appendChild(row);
            }
            for (const item of (source.items || [])) {
                candidates.push({ source, item, score: candidateScore(item, targetTitle, targetYear) });
            }
        }
        candidates.sort((a, b) => b.score - a.score ||
            String(a.source.source_name || '').localeCompare(String(b.source.source_name || ''), 'zh-CN'));
        $('#maccms-watch-status').textContent = candidates.length
            ? `找到 ${candidates.length} 条候选，已按片名和年份等信息排序`
            : '没有找到匹配影片';
        for (let index = 0; index < candidates.length; index++) {
            const candidate = candidates[index];
            const card = document.createElement('article');
            card.className = 'rounded-xl border border-slate-800 bg-slate-900/60 p-3';
            if (index === 0) card.classList.add('border-emerald-800/80');
            const heading = document.createElement('div');
            heading.className = 'flex items-start gap-2';
            const textWrap = document.createElement('div');
            textWrap.className = 'min-w-0 flex-1';
            addText(textWrap, 'div', 'break-words text-xs font-semibold leading-5 text-white', candidate.item.title);
            addText(textWrap, 'div', 'mt-1 text-[10px] leading-5 text-slate-400', candidateSubtitle(candidate));
            heading.appendChild(textWrap);
            if (index === 0) {
                const badge = addText(heading, 'span', 'shrink-0 rounded-md bg-emerald-950 px-2 py-1 text-[10px] text-emerald-300', '优先尝试');
                badge.title = '按片名、年份和返回的线路信息排序，未验证实际播放';
            }
            card.appendChild(heading);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'mt-2 w-full rounded-lg border border-slate-700 bg-slate-800/80 px-3 py-2 text-left text-[11px] font-medium text-slate-200 hover:border-emerald-700 hover:bg-slate-800';
            button.textContent = selectedCandidate === candidate ? '当前已选择' : '选择此影片';
            if (selectedCandidate === candidate) button.classList.add('border-emerald-700', 'text-emerald-300');
            button.addEventListener('click', () => chooseMovie(candidate));
            card.appendChild(button);
            root.appendChild(card);
        }
        if (!candidates.length) {
            const empty = addText(root, 'div', 'rounded-xl border border-slate-800 p-4 text-xs leading-6 text-slate-400',
                '暂时没有候选影片。可检查采集源是否启用，或尝试更短的片名。');
            empty.setAttribute('role', 'status');
        }
        return candidates;
    }

    function renderLineChoices(detail) {
        const root = $('#maccms-watch-lines');
        root.replaceChildren();
        const lines = detail.play_sources || [];
        if (!lines.length) {
            addText(root, 'p', 'text-xs leading-5 text-amber-300', '站点没有返回可识别的播放线路。');
            return;
        }
        const lineTabs = document.createElement('div');
        lineTabs.className = 'flex flex-wrap gap-2';
        lines.forEach((line, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'rounded-lg border px-3 py-2 text-[11px] font-medium transition ' +
                (index === activeLineIndex
                    ? 'border-emerald-600 bg-emerald-950/70 text-emerald-200'
                    : 'border-slate-700 bg-slate-950 text-slate-300 hover:bg-slate-800');
            button.textContent = line.name || `线路 ${index + 1}`;
            button.setAttribute('aria-pressed', String(index === activeLineIndex));
            button.addEventListener('click', () => {
                activeLineIndex = index;
                renderLineChoices(currentDetail);
            });
            lineTabs.appendChild(button);
        });
        root.appendChild(lineTabs);
        const activeLine = lines[activeLineIndex] || lines[0];
        const episodes = document.createElement('div');
        episodes.className = 'space-y-2';
        addText(episodes, 'div', 'text-[10px] text-slate-400', `${activeLine.name || '当前线路'} · ${(activeLine.episodes || []).length} ${isEpisodicMedia() ? '个选集' : '个播放项'}`);
        for (const episode of (activeLine.episodes || [])) {
            if (!/^https?:\/\//i.test(episode.url || '')) continue;
            const row = document.createElement('div');
            row.className = 'flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-950/70 p-2.5';
            const label = addText(row, 'div', 'min-w-0 flex-1 truncate text-xs text-slate-200', episode.name || '正片');
            label.title = episode.name || '正片';
            const play = document.createElement('button');
            play.type = 'button';
            play.dataset.playUrl = episode.url;
            play.className = 'shrink-0 rounded-lg bg-emerald-600 px-3 py-2 text-[11px] font-semibold text-white hover:bg-emerald-500';
            play.innerHTML = '<i class="fa-solid fa-play mr-1"></i>播放';
            play.addEventListener('click', () => {
                if (isEpisodicMedia()) playSelectedEpisode(episode, activeLineIndex);
                else playUrl(episode.url, episode.name, detail.title);
            });
            row.appendChild(play);
            episodes.appendChild(row);
        }
        if (!episodes.childElementCount) addText(episodes, 'p', 'text-xs text-slate-500', '这条线路没有可识别的播放项。');
        root.appendChild(episodes);
    }

    async function chooseMovie(candidate) {
        autoPlayActive = false;
        autoPlayEpisodeMode = false;
        autoPlayGeneration++;
        const requestId = ++detailRequest;
        selectedCandidate = candidate;
        currentDetail = null;
        currentSourceName = candidate.source.source_name || '采集站';
        activeLineIndex = 0;
        resetVideo();
        $('#maccms-watch-placeholder').classList.remove('hidden');
        $('#maccms-watch-selected-title').textContent = candidate.item.title || '影片详情';
        $('#maccms-watch-selected-meta').textContent = candidateSubtitle(candidate);
        playerMessage.textContent = `正在读取 ${currentSourceName} 的线路信息…`;
        setAutoProgress('match', '正在读取影片详情', `资源站：${currentSourceName} · 正在获取播放线路`);
        $('#maccms-watch-lines').replaceChildren();
        renderCandidatesFromSelection();
        try {
            const params = new URLSearchParams({ source_id: candidate.source.source_id, vod_id: candidate.item.id });
            const result = await fetchJson('/api/maccms/detail?' + params.toString());
            if (requestId !== detailRequest) return;
            currentDetail = result.detail;
            currentSourceName = result.source_name || currentSourceName;
            $('#maccms-watch-selected-title').textContent = currentDetail.title || candidate.item.title || '影片详情';
            $('#maccms-watch-selected-meta').textContent = [currentSourceName, currentDetail.year || candidate.item.year].filter(Boolean).join(' · ');
            playerMessage.textContent = '已取得线路信息。选择线路后，点击“播放”开始观看。线路尚未经过实际播放验证。';
            setAutoProgress('play', '已找到可选线路', '详情已加载，请选择线路并点击播放。');
            $('#maccms-watch-placeholder').classList.remove('hidden');
            renderLineChoices(currentDetail);
        } catch (error) {
            if (requestId !== detailRequest) return;
            playerMessage.textContent = (error.message || '读取播放线路失败。') + ' 可以直接选择其他匹配影片。';
            setAutoProgress('matchFailed', '读取影片详情失败', (error.message || '请求失败') + '；可以选择其他候选资源。');
            addText($('#maccms-watch-lines'), 'p', 'text-xs leading-5 text-amber-300', '详情读取失败；请尝试同一影片的其他采集站。');
        }
    }

    function renderCandidatesFromSelection() {
        const buttons = $('#maccms-watch-results').querySelectorAll('article button');
        buttons.forEach(button => {
            const card = button.closest('article');
            const title = card && card.querySelector('.text-xs.font-semibold');
            if (title && selectedCandidate && title.textContent === selectedCandidate.item.title) {
                button.textContent = '当前已选择';
            }
        });
    }

    async function playUrl(url, itemName, title, automatic = false) {
        if (!automatic) autoPlayActive = false;
        resetVideo();
        $('#maccms-watch-placeholder').classList.add('hidden');
        playerMessage.textContent = `正在尝试播放：${title || ''} · ${itemName || ''}`;
        setAutoProgress('play', '正在尝试播放', [title, itemName].filter(Boolean).join(' · '));
        if (window.MovieSyncPlayback && typeof window.MovieSyncPlayback.playOnline === 'function') {
            try {
                await window.MovieSyncPlayback.playOnline(url, title, itemName);
                playerMessage.textContent = `正在尝试：${title || ''} · ${itemName || ''}`;
            } catch (error) {
                if (automatic) handleAutoPlaybackFailure(error);
                else playerMessage.textContent = (error.message || '无法启动这条线路。') + ' 可直接切换线路或播放项。';
            }
            return;
        }
        const isHls = /\.m3u8(?:$|[?#])/i.test(url);
        if (isHls && !video.canPlayType('application/vnd.apple.mpegurl')) {
            try {
                if (!window.Hls) {
                    await new Promise((resolve, reject) => {
                        const script = document.createElement('script');
                        script.src = 'https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js';
                        script.onload = resolve;
                        script.onerror = () => reject(new Error('HLS 播放组件加载失败'));
                        document.head.appendChild(script);
                    });
                }
                if (!window.Hls.isSupported()) throw new Error('当前浏览器不支持 HLS 播放');
                activeHls = new window.Hls();
                activeHls.loadSource(url);
                activeHls.attachMedia(video);
                activeHls.on(window.Hls.Events.MANIFEST_PARSED, () => {
                    video.play().catch(() => {
                        playerMessage.textContent = '线路已载入，请点击播放器上的播放按钮。';
                    });
                });
                activeHls.on(window.Hls.Events.ERROR, (_event, data) => {
                    if (data && data.fatal) playerMessage.textContent = '这条线路播放失败；可在上方直接切换线路或播放项。';
                });
            } catch (error) {
                playerMessage.textContent = (error.message || '无法启动 HLS 播放。') + ' 可尝试其他线路。';
            }
            return;
        }
        video.onerror = () => {
            playerMessage.textContent = '这条线路加载失败或格式不受浏览器支持；可直接切换线路或播放项。';
        };
        video.src = url;
        video.load();
        video.play().then(() => {
            playerMessage.textContent = `正在播放：${title || ''} · ${itemName || ''}`;
        }).catch(() => {
            playerMessage.textContent = '浏览器未能自动开始播放，请点击播放器播放按钮；若失败，可直接切换线路。';
        });
    }

    async function searchTitle(title, year = '', autoPlay = true, requestedMediaType = mediaType) {
        mediaType = resolveMediaType(requestedMediaType);
        autoPlayEpisodeMode = false;
        const value = String(title || '').trim();
        if (!value) { playerMessage.textContent = '请输入影片或剧集名称后再搜索。'; return; }
        const requestId = ++searchRequest;
        autoPlayActive = false;
        autoPlayGeneration++;
        openModal(value);
        setAutoProgress('search', '正在搜索在线资源', `影片：${value}${year ? ' · ' + year : ''}。正在查询已启用的资源站，结果返回后会自动匹配并尝试播放。`);
        $('#maccms-watch-search-input').value = value;
        $('#maccms-watch-search-year').value = String(year || '');
        try {
            const result = await fetchJson('/api/maccms/search?' + new URLSearchParams({ title: value }));
            if (requestId !== searchRequest) return;
            const candidates = renderCandidates(result.results, value, year || '');
            if (candidates.length) {
                const bestCard = $('#maccms-watch-results').firstElementChild;
                if (bestCard) bestCard.classList.add('ring-1', 'ring-emerald-900/70');
                if (isEpisodicMedia()) {
                    playerMessage.textContent = '已找到候选剧集资源，正在读取优先匹配项的线路与选集；不会自动播放，以免误播其他集数。';
                    setAutoProgress('match', `已找到 ${candidates.length} 条候选资源`, '正在读取优先匹配项的详情，加载完成后可选择线路和集数。');
                    chooseMovie(candidates[0]);
                } else {
                    playerMessage.textContent = '已按片名、年份和线路信息排序，正在自动尝试最佳匹配；失败会依次切换后续资源。';
                    setAutoProgress('match', `已找到 ${candidates.length} 条候选资源`, '正在按片名、年份和线路信息匹配，并依次读取线路。');
                    if (autoPlay) startAutoPlayback(candidates);
                }
            } else {
                playerMessage.textContent = '没有找到候选影片。你可以检查站点设置或尝试更短的片名。';
                setAutoProgress('searchFailed', '没有找到匹配资源', '可以换个片名搜索，或检查已启用的采集源。');
            }
        } catch (error) {
            if (requestId !== searchRequest) return;
            $('#maccms-watch-status').textContent = error.message || '搜索采集源失败。';
            playerMessage.textContent = '搜索失败，请稍后重试或检查采集源配置。';
            setAutoProgress('searchFailed', '搜索在线资源失败', (error.message || '请求失败') + '；可以稍后重试或手动搜索。');
        }
    }

    function startAutoPlayback(candidates) {
        autoPlayEpisodeMode = false;
        autoPlayGeneration++; autoPlayCandidates = candidates.slice(); autoPlayCandidateIndex = 0;
        autoPlayUrls = []; autoPlayUrlIndex = 0; autoPlayActive = true;
        tryNextAutoResource(autoPlayGeneration);
    }

    async function tryNextAutoResource(generation) {
        if (!autoPlayActive || generation !== autoPlayGeneration) return;
        if (autoPlayEpisodeMode && autoPlayUrlIndex >= autoPlayUrls.length) {
            autoPlayActive = false;
            playerMessage.textContent = '本集在可确认的线路中均未能播放。没有自动跳到其他集数；请手动选择本集的其他线路或播放项。';
            setAutoProgress('playFailed', '本集暂时无法播放', '已停止本集的线路重试，没有跳到下一集。');
            return;
        }
        if (autoPlayUrlIndex >= autoPlayUrls.length) {
            const candidate = autoPlayCandidates[autoPlayCandidateIndex++];
            if (!candidate) { autoPlayActive = false; playerMessage.textContent = '所有匹配资源都尝试失败了。可以手动选择其他影片或检查采集源。'; $('#maccms-watch-status').textContent = '已尝试所有候选资源，均未能开始播放'; setAutoProgress('playFailed', '暂时无法自动播放', '已尝试匹配资源。你仍可以手动选择影片、切换线路或点击播放重试。'); return; }
            selectedCandidate = candidate; currentDetail = null; currentSourceName = candidate.source.source_name || '采集站'; activeLineIndex = 0;
            $('#maccms-watch-selected-title').textContent = candidate.item.title || '影片详情'; $('#maccms-watch-selected-meta').textContent = candidateSubtitle(candidate);
            playerMessage.textContent = '正在读取匹配资源：' + (candidate.item.title || '');
            setAutoProgress('match', '正在读取匹配资源', `资源站：${currentSourceName} · ${candidate.item.title || ''}`);
            try {
                const params = new URLSearchParams({ source_id: candidate.source.source_id, vod_id: candidate.item.id });
                const result = await fetchJson('/api/maccms/detail?' + params.toString());
                if (!autoPlayActive || generation !== autoPlayGeneration) return;
                currentDetail = result.detail; currentSourceName = result.source_name || currentSourceName;
                $('#maccms-watch-selected-title').textContent = currentDetail.title || candidate.item.title || '影片详情';
                $('#maccms-watch-selected-meta').textContent = [currentSourceName, currentDetail.year || candidate.item.year].filter(Boolean).join(' · ');
                renderLineChoices(currentDetail); autoPlayUrls = [];
                for (const line of (currentDetail.play_sources || [])) for (const episode of (line.episodes || [])) {
                    if (/^https?:\/\//i.test(episode.url || '')) autoPlayUrls.push({ url: episode.url, name: [line.name, episode.name].filter(Boolean).join(' · ') || '正片', title: currentDetail.title || candidate.item.title || '' });
                }
                autoPlayUrlIndex = 0;
            } catch (error) { if (!autoPlayActive || generation !== autoPlayGeneration) return; playerMessage.textContent = '读取资源失败，正在尝试下一项：' + (error.message || '详情请求失败'); return tryNextAutoResource(generation); }
            if (!autoPlayUrls.length) return tryNextAutoResource(generation);
        }
        const resource = autoPlayUrls[autoPlayUrlIndex++]; if (!resource) return tryNextAutoResource(generation);
        $('#maccms-watch-placeholder').classList.remove('hidden'); playerMessage.textContent = '正在尝试资源 ' + autoPlayUrlIndex + '/' + autoPlayUrls.length + '：' + resource.name;
        setAutoProgress('play', `正在尝试第 ${autoPlayUrlIndex} / ${autoPlayUrls.length} 条播放资源`, resource.name + ' · 等待播放器确认播放结果');
        try { await playUrl(resource.url, resource.name, resource.title, true); } catch (error) { handleAutoPlaybackFailure(error); }
    }

    function playSelectedEpisode(episode, sourceIndex) {
        autoPlayGeneration++;
        autoPlayEpisodeMode = true;
        autoPlayActive = true;
        autoPlayCandidates = [];
        autoPlayCandidateIndex = 0;
        autoPlayUrlIndex = 0;
        const lines = currentDetail && Array.isArray(currentDetail.play_sources) ? currentDetail.play_sources : [];
        const selectedLine = lines[sourceIndex] || lines[0];
        const episodeName = String(episode.name || '未命名播放项');
        const title = currentDetail && currentDetail.title || (selectedCandidate && selectedCandidate.item.title) || '';
        const addResource = (line, item) => {
            if (!item || !/^https?:\/\//i.test(item.url || '')) return;
            if (autoPlayUrls.some(resource => resource.url === item.url)) return;
            autoPlayUrls.push({url: item.url, name: [line.name, item.name].filter(Boolean).join(' · ') || episodeName, title});
        };
        autoPlayUrls = [];
        addResource(selectedLine, episode);
        const selectedKey = episodeMatchKey(episode.name);
        if (selectedKey) {
            lines.forEach((line, index) => {
                if (index === sourceIndex) return;
                const matches = (line.episodes || []).filter(item => episodeMatchKey(item.name) === selectedKey && /^https?:\/\//i.test(item.url || ''));
                if (matches.length === 1) addResource(line, matches[0]);
            });
        }
        if (!autoPlayUrls.length) {
            autoPlayActive = false;
            playerMessage.textContent = `“${episodeName}”没有有效的 HTTP/HTTPS 播放地址，请尝试该线路的其他播放项。`;
            setAutoProgress('playFailed', '播放地址无效', '资源站没有为当前选集提供可用的 HTTP/HTTPS 地址。');
            return;
        }

        if (autoPlayUrls.length > 1) {
            playerMessage.textContent = `已选择 ${episodeName}，正在播放所选集；失败时只尝试其他线路中可确认的同一集。`;
        } else {
            playerMessage.textContent = `正在播放所选集：${episodeName}。没有找到可确认的同集备用线路，不会自动尝试其他集数。`;
        }

        // Start the selected URL directly in the click handler's call chain so the
        // browser's user-activation allowance is preserved (especially for HLS).
        // Set the fallback cursor first; playback errors can then continue with the
        // remaining same-episode URLs through handleAutoPlaybackFailure().
        const selectedResource = autoPlayUrls[0];
        autoPlayUrlIndex = 1;
        $('#maccms-watch-placeholder').classList.add('hidden');
        setAutoProgress('play', '正在播放所选集', selectedResource.name + ' · 等待播放器确认播放结果');
        void playUrl(selectedResource.url, selectedResource.name, selectedResource.title, true);
    }

    function handleAutoPlaybackFailure(error) { if (!autoPlayActive) return; playerMessage.textContent = (error && error.message ? error.message + '；' : '') + '当前资源失败，正在自动切换下一条…'; setAutoProgress('play', '当前线路未能播放，正在切换', (error && error.message ? error.message + ' · ' : '') + '正在尝试下一条资源。'); tryNextAutoResource(autoPlayGeneration); }
    function handleAutoPlaybackStarted() { playerMessage.textContent = autoPlayEpisodeMode ? '本集资源已开始播放。' : '资源已开始播放。'; setAutoProgress('success', '视频已开始播放', '播放已由播放器事件确认；如果之后中断，可以手动切换线路。'); }

    async function movieDetailWatch() {
        const movie = Array.isArray(moviesData) && Number.isInteger(activeMovieDetailIndex) ? moviesData[activeMovieDetailIndex] : null;
        if (!movie) return;
        const category = String(movie.tag || currentTag || '').trim();
        const type = resolveMediaType(category);
        if (type === 'movie' && category && !/电影|movie/i.test(category)) {
            showToast('该分类暂未接入在线点播。当前支持电影、电视剧、动漫和综艺。', 'info');
            return;
        }
        const params = new URLSearchParams({ title: movie.title || '', year: movie.year || movie.release_year || '', type: type });
        window.location.assign('/playback?' + params.toString());
    }

    function openOnline(title = '', year = '', requestedType = mediaType) {
        mediaType = resolveMediaType(requestedType);
        ensureModal();
        if (inlineMode && window.MovieSyncPlayback && window.MovieSyncPlayback.activateOnline) {
            window.MovieSyncPlayback.activateOnline();
        }
        modal.classList.remove('hidden');
        if (title) searchTitle(title, year, mediaType === 'movie', mediaType);
        else {
            $('#maccms-watch-search-input').focus();
            playerMessage.textContent = '输入影片或剧集名称搜索采集源；匹配后选择线路与播放项。';
        }
    }

    window.movieDetailWatch = movieDetailWatch;
    window.MovieSyncPlayback = window.MovieSyncPlayback || {};
    window.MovieSyncPlayback.openOnline = openOnline;
    window.MovieSyncPlayback.cancelAutoPlayback = () => {
        autoPlayActive = false;
        autoPlayEpisodeMode = false;
        autoPlayGeneration++;
        searchRequest++;
        detailRequest++;
        resetVideo();
    };
    window.MovieSyncPlayback.onOnlinePlaybackFailure = handleAutoPlaybackFailure;
    window.MovieSyncPlayback.onOnlinePlaybackStarted = handleAutoPlaybackStarted;
})();
