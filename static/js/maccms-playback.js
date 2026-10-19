/* Movie-only MacCMS immediate playback. Keeps transfer/chase flows untouched. */
(() => {
    let modal;
    let video;
    let playerMessage;
    let activeHls = null;
    let searchRequest = 0;

    function ensureModal() {
        if (modal) return modal;
        modal = document.createElement('div');
        modal.id = 'maccms-watch-modal';
        modal.className = 'fixed inset-0 z-[120] hidden items-center justify-center bg-black/75 p-3 backdrop-blur-sm';
        modal.innerHTML = `
            <section class="flex max-h-[94vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl">
                <header class="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
                    <div class="min-w-0"><div class="text-[10px] uppercase tracking-widest text-emerald-400">MacCMS · 即时点播</div><h3 id="maccms-watch-title" class="mt-1 truncate text-sm font-semibold text-white">立即观看</h3></div>
                    <button type="button" data-watch-close class="rounded-lg px-3 py-2 text-slate-400 hover:bg-slate-800 hover:text-white" aria-label="关闭"><i class="fa-solid fa-xmark"></i></button>
                </header>
                <div class="grid min-h-0 flex-1 gap-3 overflow-y-auto p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
                    <div class="min-w-0"><p id="maccms-watch-status" class="mb-2 text-xs leading-5 text-slate-400">正在搜索已启用的采集源…</p><div id="maccms-watch-results" class="space-y-2"></div></div>
                    <div class="min-w-0 space-y-3">
                        <div id="maccms-watch-player-wrap" class="hidden overflow-hidden rounded-xl border border-slate-800 bg-black">
                            <video id="maccms-watch-video" class="aspect-video w-full bg-black" controls playsinline preload="metadata"></video>
                        </div>
                        <p id="maccms-watch-player-message" class="text-xs leading-5 text-slate-400">选择一个匹配的影片，再选择播放线路。</p>
                        <div id="maccms-watch-lines" class="space-y-2"></div>
                    </div>
                </div>
                <footer class="border-t border-slate-800 px-4 py-3 text-[10px] leading-5 text-slate-500">播放地址来自采集站点，能否播放取决于线路可用性、浏览器格式支持及网络访问情况。</footer>
            </section>`;
        document.body.appendChild(modal);
        video = modal.querySelector('#maccms-watch-video');
        playerMessage = modal.querySelector('#maccms-watch-player-message');
        modal.querySelector('[data-watch-close]').addEventListener('click', closeModal);
        modal.addEventListener('click', event => {
            if (event.target === modal) closeModal();
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
        resetVideo();
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }

    function addText(parent, tag, className, text) {
        const node = document.createElement(tag);
        node.className = className;
        node.textContent = String(text || '');
        parent.appendChild(node);
        return node;
    }

    function openModal(title) {
        ensureModal();
        resetVideo();
        modal.querySelector('#maccms-watch-title').textContent = title || '立即观看';
        modal.querySelector('#maccms-watch-status').textContent = '正在查询启用的采集站点…';
        modal.querySelector('#maccms-watch-results').replaceChildren();
        modal.querySelector('#maccms-watch-lines').replaceChildren();
        modal.querySelector('#maccms-watch-player-wrap').classList.add('hidden');
        playerMessage.textContent = '选择一个匹配的影片，再选择播放线路。';
        modal.classList.remove('hidden');
        modal.classList.add('flex');
    }

    async function fetchJson(url) {
        const response = await apiFetch(url);
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || '请求失败');
        return result;
    }

    function renderPlayback(detail, sourceName) {
        const lines = modal.querySelector('#maccms-watch-lines');
        lines.replaceChildren();
        resetVideo();
        modal.querySelector('#maccms-watch-player-wrap').classList.add('hidden');
        playerMessage.textContent = `${sourceName} · ${detail.title || '影片'}：请选择线路和播放项。`;
        for (const line of (detail.play_sources || [])) {
            const group = document.createElement('section');
            group.className = 'rounded-xl border border-slate-800 bg-slate-900/70 p-3';
            addText(group, 'div', 'mb-2 text-xs font-semibold text-slate-200', line.name);
            const choices = document.createElement('div');
            choices.className = 'flex flex-wrap gap-2';
            for (const episode of (line.episodes || [])) {
                if (!/^https?:\/\//i.test(episode.url || '')) continue;
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'rounded-lg border border-emerald-800/70 bg-emerald-950/40 px-3 py-2 text-xs text-emerald-200 hover:bg-emerald-900/50';
                button.textContent = episode.name || '播放';
                button.addEventListener('click', () => playUrl(episode.url, episode.name, detail.title));
                choices.appendChild(button);
            }
            group.appendChild(choices);
            if (choices.childElementCount) lines.appendChild(group);
        }
        if (!lines.childElementCount) playerMessage.textContent = '这部影片没有识别到可用的 HTTP/HTTPS 播放项。';
    }

    async function playUrl(url, itemName, title) {
        resetVideo();
        modal.querySelector('#maccms-watch-player-wrap').classList.remove('hidden');
        playerMessage.textContent = `正在打开：${title || ''} · ${itemName || ''}`;
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
                    video.play().catch(() => {});
                });
                activeHls.on(window.Hls.Events.ERROR, (_event, data) => {
                    if (data && data.fatal) playerMessage.textContent = '播放流加载失败，试试同一影片的其他线路。';
                });
            } catch (error) {
                playerMessage.textContent = error.message || '无法启动 HLS 播放。';
            }
            return;
        }
        video.src = url;
        video.load();
        video.play().then(() => {
            playerMessage.textContent = `正在播放：${title || ''} · ${itemName || ''}`;
        }).catch(() => {
            playerMessage.textContent = '浏览器未能自动开始播放，请点击播放器播放按钮；若仍失败，可换一条线路。';
        });
        video.onerror = () => {
            playerMessage.textContent = '视频加载失败或格式不受浏览器支持，请尝试其他线路。';
        };
    }

    async function chooseMovie(sourceId, sourceName, item) {
        const lines = modal.querySelector('#maccms-watch-lines');
        lines.replaceChildren();
        playerMessage.textContent = `正在读取 ${sourceName} 的影片播放详情…`;
        try {
            const params = new URLSearchParams({ source_id: sourceId, vod_id: item.id });
            const result = await fetchJson('/api/maccms/detail?' + params.toString());
            renderPlayback(result.detail, result.source_name || sourceName);
        } catch (error) {
            playerMessage.textContent = error.message || '读取影片播放详情失败。';
        }
    }

    async function movieDetailWatch() {
        const movie = Array.isArray(moviesData) && Number.isInteger(activeMovieDetailIndex)
            ? moviesData[activeMovieDetailIndex] : null;
        if (!movie) return;
        const category = String(movie.tag || currentTag || '').trim();
        if (category && category !== '电影') {
            showToast('目前先打通电影点播；电视剧、综艺和动漫的线路及集数选择会在后续阶段接入。', 'info');
            return;
        }
        const requestId = ++searchRequest;
        openModal(movie.title);
        try {
            const result = await fetchJson('/api/maccms/search?' + new URLSearchParams({ title: movie.title || '' }));
            if (requestId !== searchRequest) return;
            const results = modal.querySelector('#maccms-watch-results');
            let total = 0;
            for (const source of (result.results || [])) {
                if (source.error) {
                    const errorRow = document.createElement('div');
                    errorRow.className = 'rounded-lg border border-amber-900/40 bg-amber-950/20 p-3 text-[11px] leading-5 text-amber-200';
                    errorRow.textContent = `${source.source_name || '采集源'}：${source.error}`;
                    results.appendChild(errorRow);
                }
                for (const item of (source.items || [])) {
                    total++;
                    const card = document.createElement('article');
                    card.className = 'rounded-xl border border-slate-800 bg-slate-900/70 p-3';
                    addText(card, 'div', 'text-xs font-semibold text-white', item.title);
                    addText(card, 'div', 'mt-1 text-[10px] leading-5 text-slate-400',
                        [source.source_name, item.year, item.category, item.remarks].filter(Boolean).join(' · '));
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'mt-2 rounded-lg bg-emerald-600/90 px-3 py-2 text-[11px] font-medium text-white hover:bg-emerald-500';
                    button.textContent = '选择并查看线路';
                    button.addEventListener('click', () => chooseMovie(source.source_id, source.source_name, item));
                    card.appendChild(button);
                    results.appendChild(card);
                }
            }
            modal.querySelector('#maccms-watch-status').textContent =
                `${result.message || ''}${total ? '。点击匹配影片查看线路。' : '。'}`;
            if (!total) {
                const empty = document.createElement('div');
                empty.className = 'rounded-xl border border-slate-800 p-4 text-xs leading-6 text-slate-400';
                empty.textContent = '没有找到匹配影片。你可以检查站点是否已启用，或先通过现有资源搜索与转存流程观看。';
                results.appendChild(empty);
            }
        } catch (error) {
            if (requestId !== searchRequest) return;
            modal.querySelector('#maccms-watch-status').textContent = error.message || '搜索采集源失败。';
        }
    }

    window.movieDetailWatch = movieDetailWatch;
})();
