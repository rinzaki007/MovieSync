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

    const $ = (selector) => modal.querySelector(selector);

    function ensureModal() {
        if (modal) return modal;
        const inlineRoot = document.getElementById('maccms-inline-panel');
        inlineMode = Boolean(inlineRoot);
        modal = inlineRoot || document.createElement('div');
        modal.id = inlineMode ? 'maccms-inline-panel' : 'maccms-watch-modal';
        modal.className = inlineMode ? 'hidden mt-4 min-w-0' : 'fixed inset-0 z-[120] hidden items-center justify-center bg-black/80 p-2 backdrop-blur-sm sm:p-4';
        modal.innerHTML = `
            <section class="${inlineMode ? 'w-full overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-xl' : 'flex max-h-[96vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl'}">
                <header class="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3 sm:px-5">
                    <div class="min-w-0"><div class="text-[10px] uppercase tracking-widest text-emerald-400">MOVIESYNC · MACCMS</div><h3 id="maccms-watch-title" class="mt-1 truncate text-sm font-semibold text-white">立即观看</h3></div>
                    <button type="button" data-watch-close class="rounded-lg px-3 py-2 text-slate-400 hover:bg-slate-800 hover:text-white" aria-label="关闭"><i class="fa-solid fa-xmark"></i></button>
                </header>
                <form id="maccms-watch-search-form" class="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-900/50 px-4 py-3 sm:px-5">
                    <label class="flex min-w-[150px] flex-1 items-center gap-2 rounded-lg border border-slate-700 bg-slate-950 px-3">
                        <i class="fa-solid fa-magnifying-glass text-xs text-slate-500"></i>
                        <input id="maccms-watch-search-input" type="search" placeholder="搜索电影名称…" class="w-full min-w-0 bg-transparent py-2 text-xs text-slate-200 outline-none placeholder:text-slate-600" required>
                    </label>
                    <input id="maccms-watch-search-year" type="number" min="1900" max="2099" placeholder="年份（可选）" aria-label="电影年份（可选）" class="w-28 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none placeholder:text-slate-600">
                    <button type="submit" class="rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-emerald-500"><i class="fa-solid fa-search mr-1"></i>搜索</button>
                </form>
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
        modal.classList.remove('hidden');
        if (!inlineMode) modal.classList.add('flex');
    }

    async function fetchJson(url) {
        const response = typeof apiFetch === 'function' ? await apiFetch(url) : await fetch(url, { credentials: 'same-origin' });
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || '请求失败');
        return result;
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
        if (item.category && /电影|movie/i.test(item.category)) score += 4;
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
        addText(episodes, 'div', 'text-[10px] text-slate-400', `${activeLine.name || '当前线路'} · ${(activeLine.episodes || []).length} 个播放项`);
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
            play.addEventListener('click', () => playUrl(episode.url, episode.name, detail.title));
            row.appendChild(play);
            episodes.appendChild(row);
        }
        if (!episodes.childElementCount) addText(episodes, 'p', 'text-xs text-slate-500', '这条线路没有可识别的播放项。');
        root.appendChild(episodes);
    }

    async function chooseMovie(candidate) {
        autoPlayActive = false;
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
            $('#maccms-watch-placeholder').classList.remove('hidden');
            renderLineChoices(currentDetail);
        } catch (error) {
            if (requestId !== detailRequest) return;
            playerMessage.textContent = (error.message || '读取播放线路失败。') + ' 可以直接选择其他匹配影片。';
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

    async function searchTitle(title, year = '', autoPlay = true) {
        const value = String(title || '').trim();
        if (!value) { playerMessage.textContent = '请输入电影名称后再搜索。'; return; }
        const requestId = ++searchRequest;
        autoPlayActive = false;
        autoPlayGeneration++;
        openModal(value);
        $('#maccms-watch-search-input').value = value;
        $('#maccms-watch-search-year').value = String(year || '');
        try {
            const result = await fetchJson('/api/maccms/search?' + new URLSearchParams({ title: value }));
            if (requestId !== searchRequest) return;
            const candidates = renderCandidates(result.results, value, year || '');
            if (candidates.length) {
                playerMessage.textContent = '已按片名、年份和线路信息排序，正在自动尝试最佳匹配；失败会依次切换后续资源。';
                const bestCard = $('#maccms-watch-results').firstElementChild;
                if (bestCard) bestCard.classList.add('ring-1', 'ring-emerald-900/70');
                if (autoPlay) startAutoPlayback(candidates);
            } else playerMessage.textContent = '没有找到候选影片。你可以检查站点设置或尝试更短的片名。';
        } catch (error) {
            if (requestId !== searchRequest) return;
            $('#maccms-watch-status').textContent = error.message || '搜索采集源失败。';
            playerMessage.textContent = '搜索失败，请稍后重试或检查采集源配置。';
        }
    }

    function startAutoPlayback(candidates) {
        autoPlayGeneration++; autoPlayCandidates = candidates.slice(); autoPlayCandidateIndex = 0;
        autoPlayUrls = []; autoPlayUrlIndex = 0; autoPlayActive = true;
        tryNextAutoResource(autoPlayGeneration);
    }

    async function tryNextAutoResource(generation) {
        if (!autoPlayActive || generation !== autoPlayGeneration) return;
        if (autoPlayUrlIndex >= autoPlayUrls.length) {
            const candidate = autoPlayCandidates[autoPlayCandidateIndex++];
            if (!candidate) { autoPlayActive = false; playerMessage.textContent = '所有匹配资源都尝试失败了。可以手动选择其他影片或检查采集源。'; $('#maccms-watch-status').textContent = '已尝试所有候选资源，均未能开始播放'; return; }
            selectedCandidate = candidate; currentDetail = null; currentSourceName = candidate.source.source_name || '采集站'; activeLineIndex = 0;
            $('#maccms-watch-selected-title').textContent = candidate.item.title || '影片详情'; $('#maccms-watch-selected-meta').textContent = candidateSubtitle(candidate);
            playerMessage.textContent = '正在读取匹配资源：' + (candidate.item.title || '');
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
        try { await playUrl(resource.url, resource.name, resource.title, true); } catch (error) { handleAutoPlaybackFailure(error); }
    }

    function handleAutoPlaybackFailure(error) { if (!autoPlayActive) return; playerMessage.textContent = (error && error.message ? error.message + '；' : '') + '当前资源失败，正在自动切换下一条…'; tryNextAutoResource(autoPlayGeneration); }
    function handleAutoPlaybackStarted() { if (autoPlayActive) playerMessage.textContent = '资源已开始播放；若线路随后失效，将自动尝试下一条。'; }

    async function movieDetailWatch() {
        const movie = Array.isArray(moviesData) && Number.isInteger(activeMovieDetailIndex) ? moviesData[activeMovieDetailIndex] : null;
        if (!movie) return;
        const category = String(movie.tag || currentTag || '').trim();
        if (category && category !== '电影') {
            showToast('目前先支持电影点播；电视剧、综艺和动漫的线路及集数选择会在后续阶段接入。', 'info');
            return;
        }
        const params = new URLSearchParams({ title: movie.title || '', year: movie.year || movie.release_year || '' });
        window.location.assign('/playback?' + params.toString());
    }

    function openOnline(title = '', year = '') {
        ensureModal();
        modal.classList.remove('hidden');
        if (title) searchTitle(title, year);
        else {
            $('#maccms-watch-search-input').focus();
            playerMessage.textContent = '输入电影名称搜索采集源；选择匹配影片后，再手动选择线路播放。';
        }
    }

    window.movieDetailWatch = movieDetailWatch;
    window.MovieSyncPlayback = window.MovieSyncPlayback || {};
    window.MovieSyncPlayback.openOnline = openOnline;
    window.MovieSyncPlayback.onOnlinePlaybackFailure = handleAutoPlaybackFailure;
    window.MovieSyncPlayback.onOnlinePlaybackStarted = handleAutoPlaybackStarted;
})();
