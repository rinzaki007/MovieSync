let tasks = [];
let taskFilter = 'all';
let selectedCandidate = null;
let taskRefreshTimer = null;
let taskLoadSequence = 0;
let candidateSearchSequence = 0;
let taskCategorySequence = 0;
let taskDestinationSequence = 0;
let taskStorageTargets = [];
let taskDefaultStorageTargetId = '';

document.addEventListener('DOMContentLoaded', () => {
    loadTasks();
    loadTaskCategories();
    document.getElementById('task-storage-target')?.addEventListener('change', event => {
        loadTaskDestinations(event.target.value);
    });
    const presetTitle = new URLSearchParams(window.location.search).get('title');
    if (presetTitle) setTimeout(() => openAddTask(), 120);
});

async function apiFetchTask(url, options = {}) {
    const init = { ...options, credentials: 'same-origin' };
    const method = (init.method || 'GET').toUpperCase();
    const headers = new Headers(init.headers || {});
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
        const token = document.querySelector('meta[name=csrf-token]')?.content;
        if (token) headers.set('X-CSRF-Token', token);
    }
    init.headers = headers;
    const response = await window.fetch(url, init);
    if (response.status === 401) window.location.href = '/login';
    return response;
}

function escapeTask(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function taskToast(message) {
    const box = document.getElementById('task-toast');
    const el = document.createElement('div');
    el.className = 'pointer-events-auto rounded-lg border border-slate-700 bg-slate-900/95 px-4 py-3 text-xs text-slate-200 shadow-2xl';
    el.textContent = String(message || '');
    box.appendChild(el);
    setTimeout(() => el.remove(), 3200);
}

async function loadTaskCategories() {
    const requestId = ++taskCategorySequence;
    const targetSelect = document.getElementById('task-storage-target');
    const destinationSelect = document.getElementById('task-target-fid');
    if (!targetSelect || !destinationSelect) return;
    try {
        const resp = await apiFetchTask('/api/storage-targets');
        const result = await resp.json();
        if (requestId !== taskCategorySequence) return;
        if (!resp.ok || !result.success) throw new Error(result.message || '读取转存位置失败');

        const targets = (Array.isArray(result.targets) ? result.targets : [])
            .filter(target => target && target.id && target.enabled !== false && !target.config_error);
        taskStorageTargets = targets;
        taskDefaultStorageTargetId = String(result.default_target_id || '');
        targetSelect.replaceChildren();
        if (!targets.length) {
            targetSelect.add(new Option('没有可用的转存位置', ''));
            targetSelect.disabled = true;
            destinationSelect.replaceChildren(new Option('请先启用转存卡片', ''));
            destinationSelect.disabled = true;
            document.getElementById('task-form-status').textContent = '请先在「卡片管理」中启用并配置一个转存卡片。';
            refreshTaskSubmitState();
            return;
        }

        for (const target of targets) {
            targetSelect.add(new Option(target.name || target.id, target.id));
        }
        const preferred = targets.find(target => target.id === result.default_target_id) || null;
        if (!preferred && result.default_target_id) {
            targetSelect.add(new Option('默认位置不可用，请手动选择', ''));
            targetSelect.value = '';
            document.getElementById('task-form-status').textContent =
                '默认转存位置不可用；请手动选择一个已启用且支持当前资源类型的存储卡片。系统不会自动改存到其他网盘。';
        } else if (preferred) {
            targetSelect.value = preferred.id;
        } else if (targets.length === 1) {
            targetSelect.value = targets[0].id;
        } else {
            targetSelect.add(new Option('请选择转存位置', ''));
            targetSelect.value = '';
        }
        targetSelect.disabled = false;
        if (targetSelect.value) await loadTaskDestinations(targetSelect.value);
        else {
            destinationSelect.replaceChildren(new Option('请先选择转存位置', ''));
            destinationSelect.disabled = true;
        }
        if (selectedCandidate) filterStorageTargetsForCandidate(selectedCandidate.candidate);
    } catch (error) {
        if (requestId !== taskCategorySequence) return;
        targetSelect.replaceChildren(new Option('读取转存位置失败', ''));
        targetSelect.disabled = true;
        destinationSelect.replaceChildren(new Option('无法读取保存目录', ''));
        destinationSelect.disabled = true;
        document.getElementById('task-form-status').textContent = error.message || '读取保存目录失败，请检查卡片配置。';
        refreshTaskSubmitState();
    }
}

function filterStorageTargetsForCandidate(candidate) {
    const targetSelect = document.getElementById('task-storage-target');
    const destinationSelect = document.getElementById('task-target-fid');
    if (!targetSelect || !destinationSelect) return;

    const resourceType = String(candidate?.resource_type || '').trim().toLowerCase();
    const required = resourceType ? 'storage.accepts.' + resourceType : '';
    const compatible = taskStorageTargets.filter(target => {
        if (!required) return true;
        const capabilities = Array.isArray(target.capabilities) ? target.capabilities : [];
        return capabilities.includes(required) || capabilities.includes('storage.accepts.*');
    });

    targetSelect.replaceChildren();
    if (!compatible.length) {
        targetSelect.add(new Option(
            resourceType
                ? '没有存储卡片支持此资源类型'
                : '没有可用的转存位置',
            ''
        ));
        targetSelect.disabled = true;
        destinationSelect.replaceChildren(new Option('请安装或启用兼容的存储卡片', ''));
        destinationSelect.disabled = true;
        document.getElementById('task-form-status').textContent = resourceType
            ? '当前资源类型为 ' + resourceType + '，没有已启用的存储卡片声明支持它。请安装对应卡片后再试。'
            : '请先在「卡片管理」中启用并配置一个转存卡片。';
        refreshTaskSubmitState();
        return false;
    }

    for (const target of compatible) {
        targetSelect.add(new Option(target.name || target.id, target.id));
    }
    const preferred = compatible.find(target => target.id === candidate?.storage_target_id)
        || compatible.find(target => target.id === taskDefaultStorageTargetId)
        || (compatible.length === 1 ? compatible[0] : null);
    targetSelect.disabled = false;
    if (preferred) {
        targetSelect.value = preferred.id;
        loadTaskDestinations(preferred.id);
    } else {
        targetSelect.add(new Option('请选择兼容的转存位置', ''));
        targetSelect.value = '';
        destinationSelect.replaceChildren(new Option('请先选择转存位置', ''));
        destinationSelect.disabled = true;
        document.getElementById('task-form-status').textContent =
            '此资源有多个兼容的存储卡片，请明确选择目标后再提交。';
    }
    refreshTaskSubmitState();
    return true;
}

async function loadTaskDestinations(targetId) {
    const requestId = ++taskDestinationSequence;
    const targetSelect = document.getElementById('task-storage-target');
    const destinationSelect = document.getElementById('task-target-fid');
    if (!targetSelect || !destinationSelect || !targetId) {
        if (destinationSelect) {
            destinationSelect.replaceChildren(new Option('请先选择转存位置', ''));
            destinationSelect.disabled = true;
        }
        refreshTaskSubmitState();
        return;
    }
    destinationSelect.disabled = true;
    destinationSelect.replaceChildren(new Option('正在读取保存目录…', ''));
    refreshTaskSubmitState();
    try {
        const resp = await apiFetchTask('/api/storage-targets/' + encodeURIComponent(targetId) + '/destinations');
        const result = await resp.json();
        if (requestId !== taskDestinationSequence || targetSelect.value !== targetId) return;
        if (!resp.ok || !result.success) throw new Error(result.message || '读取保存目录失败');
        const destinations = Array.isArray(result.destinations) ? result.destinations : [];
        destinationSelect.replaceChildren();
        if (!destinations.length) {
            destinationSelect.add(new Option('该转存位置没有可用目录', ''));
            destinationSelect.disabled = true;
            document.getElementById('task-form-status').textContent = '当前转存位置没有可用目录，请检查卡片配置。';
            refreshTaskSubmitState();
            return;
        }
        for (const destination of destinations) {
            if (!destination || destination.id === undefined || destination.id === null) continue;
            destinationSelect.add(new Option(destination.name || String(destination.id), String(destination.id)));
        }
        if (!destinationSelect.options.length) {
            destinationSelect.add(new Option('该转存位置没有可用目录', ''));
            destinationSelect.disabled = true;
        } else {
            const preferred = destinations.find(item => item.is_default)
                || destinations.find(item => item.category === '电视剧')
                || destinations[0];
            destinationSelect.value = String(preferred.id);
            destinationSelect.disabled = false;
            document.getElementById('task-form-status').textContent = '';
        }
        refreshTaskSubmitState();
    } catch (error) {
        if (requestId !== taskDestinationSequence) return;
        destinationSelect.replaceChildren(new Option('读取目录失败', ''));
        destinationSelect.disabled = true;
        document.getElementById('task-form-status').textContent = error.message || '读取保存目录失败，请重试。';
        refreshTaskSubmitState();
    }
}

function refreshTaskSubmitState() {
    const button = document.getElementById('task-submit');
    if (!button) return;
    const selectedCount = selectedCandidate?.selectedFids?.size || 0;
    const targetSelect = document.getElementById('task-storage-target');
    const destinationSelect = document.getElementById('task-target-fid');
    const ready = selectedCount > 0
        && Boolean(targetSelect?.value)
        && !targetSelect?.disabled
        && Boolean(destinationSelect?.value)
        && !destinationSelect?.disabled;
    button.disabled = !ready;
    button.classList.toggle('opacity-40', !ready);
}

async function loadTasks() {
    const requestId = ++taskLoadSequence;
    const list = document.getElementById('task-list');
    try {
        const resp = await apiFetchTask('/api/tasks');
        const res = await resp.json();
        if (!res.success) throw new Error(res.message || '加载失败');
        if (requestId !== taskLoadSequence) return;
        tasks = Array.isArray(res.tasks) ? res.tasks : [];
        renderStats();
        renderTasks();
        renderHistory();
        const running = tasks.some(t => t.status === 'running' || t.status === 'queued');
        if (running && !taskRefreshTimer) {
            taskRefreshTimer = setInterval(() => loadTasks(), 2000);
        } else if (!running && taskRefreshTimer) {
            clearInterval(taskRefreshTimer);
            taskRefreshTimer = null;
        }
    } catch (err) {
        if (requestId === taskLoadSequence && list) {
            list.innerHTML = '<div class="p-10 text-center text-xs text-rose-400">任务加载失败，请刷新重试。</div>';
        }
    }
}

function taskState(task) {
    if (task.pending_save_keys && task.pending_save_keys.length && task.pending_save_uncertain) return {key:'pending', label:'结果待核实', cls:'text-amber-200 bg-amber-950/60 border-amber-700'};
    if (task.pending_save_keys && task.pending_save_keys.length) return {key:'pending', label:'待确认转存', cls:'text-amber-300 bg-amber-950/50 border-amber-800'};
    if (task.recovery_uncertain) return {key:'uncertain', label:'结果待核实', cls:'text-amber-200 bg-amber-950/60 border-amber-700'};
    if (task.status === 'running') return {key:'running', label:'执行中', cls:'text-purple-300 bg-purple-950/50 border-purple-800'};
    if (task.status === 'failed' || task.last_error) return {key:'error', label:'异常', cls:'text-rose-300 bg-rose-950/50 border-rose-800'};
    if (task.status === 'success') return {key:'success', label:'已完成', cls:'text-emerald-300 bg-emerald-950/50 border-emerald-800'};
    if (task.status === 'queued') return {key:'waiting', label:'排队中', cls:'text-blue-300 bg-blue-950/50 border-blue-800'};
    if (task.next_run_at && Number(task.next_run_at) <= Date.now()/1000) return {key:'soon', label:'即将检查', cls:'text-amber-300 bg-amber-950/50 border-amber-800'};
    return {key:'waiting', label:'等待检查', cls:'text-blue-300 bg-blue-950/50 border-blue-800'};
}

function formatNextRun(ts) {
    if (!ts) return '等待调度';
    const diff = Math.max(0, Math.round(Number(ts) - Date.now()/1000));
    if (diff < 60) return '不到 1 分钟';
    const hours = Math.floor(diff / 3600);
    const mins = Math.floor((diff % 3600) / 60);
    if (hours) return hours + ' 小时 ' + mins + ' 分钟后';
    return mins + ' 分钟后';
}

function renderStats() {
    const smartTasks = tasks.filter(t => t.type === 'subscription');
    const total = tasks.length;
    const error = tasks.filter(t => ['error','failed'].includes(t.status) || !!t.last_error).length;
    const waiting = tasks.filter(t => ['waiting','queued'].includes(t.status)).length;
    const soon = tasks.filter(t => ['soon','pending','running'].includes(taskState(t).key) || ['running','pending'].includes(t.status)).length;
    document.getElementById('stat-total').textContent = total;
    document.getElementById('stat-waiting').textContent = waiting;
    document.getElementById('stat-error').textContent = error;
    document.getElementById('stat-soon').textContent = soon;
}

function setFilter(filter) {
    taskFilter = filter;
    document.querySelectorAll('.task-filter').forEach(btn => {
        const active = btn.dataset.filter === filter;
        btn.className = active ? 'task-filter rounded-md bg-slate-700 px-3 py-1.5 text-slate-100' : 'task-filter rounded-md px-3 py-1.5 text-slate-400 hover:text-white';
    });
    renderTasks();
}

function renderTasks() {
    const lists = {
        transfer: document.getElementById('task-list'),
        subscription: document.getElementById('smart-task-list')
    };
    Object.values(lists).forEach(list => {
        if (list) list.innerHTML = '';
    });

    let visible = tasks.slice();
    if (taskFilter === 'error') {
        visible = visible.filter(t => ['error','failed'].includes(t.status) || !!t.last_error);
    }
    if (taskFilter === 'waiting') {
        visible = visible.filter(t => ['waiting','queued'].includes(t.status));
    }
    if (taskFilter === 'running') {
        visible = visible.filter(
            t => ['running','pending'].includes(taskState(t).key) || t.status === 'running'
        );
    }

    const groups = [
        {type: 'transfer', empty: '暂无普通转存任务。'},
        {type: 'subscription', empty: '暂无智能追剧任务。'}
    ];

    groups.forEach(group => {
        const list = lists[group.type];
        if (!list) return;
        const groupTasks = visible.filter(task => task.type === group.type);
        if (!groupTasks.length) {
            list.innerHTML = '<div class="p-10 text-center text-xs text-slate-500">' + group.empty + '</div>';
            return;
        }

        groupTasks.forEach(task => {
            const state = taskState(task);
            const retryable = task.type === 'transfer' && task.status === 'failed' && !task.recovery_uncertain;
            const card = document.createElement('article');
            card.className = 'm-2 rounded-2xl border border-slate-800/80 bg-slate-950/30 p-4 transition hover:border-slate-600';
            const phase = escapeTask(task.phase_label || task.message || '等待执行');
            const cover = task.cover ? '/api/proxy-img?url=' + encodeURIComponent(task.cover) : '';
            card.innerHTML =
                '<div class="flex gap-3">' +
                (cover ? '<img src="' + cover + '" class="h-20 w-14 shrink-0 rounded-lg object-cover bg-slate-950" onerror="this.style.display=\'none\'">' : '') +
                '<div class="min-w-0 flex-1">' +
                '<div class="flex flex-wrap items-center gap-2">' +
                '<h3 class="truncate text-sm font-semibold text-slate-100">' + escapeTask(task.title) + '</h3>' +
                '<span class="rounded-full border px-2 py-0.5 text-[10px] ' + state.cls + '">' + state.label + '</span>' +
                '<span class="rounded-full border border-slate-700 bg-slate-950 px-2 py-0.5 text-[10px] text-slate-400">' + escapeTask(task.kind || '任务') + '</span>' +
                '</div>' +
                renderTaskProgressBar(task) +
                '<div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">' +
                '<span class="text-slate-300">' + phase + '</span>' +
                '<span>成功 ' + Number(task.success_count || 0) + '</span><span>跳过 ' + Number(task.skipped_count || 0) + '</span><span>失败 ' + Number(task.failed_count || 0) + '</span>' +
                (task.total ? '<span>文件 ' + Number(task.total) + '</span>' : '') +
                '</div>' +
                '<div class="mt-2 text-[11px] ' + ((task.status === 'failed' || task.last_error) ? 'text-rose-400' : 'text-slate-500') + '">' + escapeTask(task.message || task.last_error || '') + '</div>' +
                '</div>' +
                '<div class="flex shrink-0 flex-wrap content-start justify-end gap-2">' +
                '<button data-action="detail" data-id="' + escapeTask(task.id) + '" class="rounded-xl border border-slate-700/70 bg-slate-950/55 px-3 py-2 text-[11px] text-slate-300 hover:bg-slate-800/80"><i class="fa-solid fa-circle-info mr-1"></i>详情</button>' +
                (task.type === 'subscription' && task.pending_save_uncertain ? '<button data-action="confirm-saved" data-id="' + escapeTask(task.subscription_id) + '" class="rounded-xl border border-emerald-800/70 bg-emerald-950/35 px-3 py-2 text-[11px] text-emerald-300 hover:bg-emerald-900/60">确认已转存</button><button data-action="retry-pending" data-id="' + escapeTask(task.subscription_id) + '" class="rounded-xl border border-amber-800/70 bg-amber-950/35 px-3 py-2 text-[11px] text-amber-300 hover:bg-amber-900/60">确认未转存，重试</button>' : '') +
                (task.type === 'subscription' && !task.pending_save_uncertain ? '<button data-action="run-sub" data-id="' + escapeTask(task.subscription_id) + '" class="rounded-xl bg-purple-600/90 px-3 py-2 text-[11px] font-medium text-white shadow-lg shadow-purple-600/15 hover:bg-purple-500"><i class="fa-solid fa-play mr-1"></i>立即检查</button>' : '') +
                ((task.type === 'subscription' || task.status === 'success' || task.status === 'failed' || task.status === 'error') ? '<button data-action="delete" data-id="' + escapeTask(task.id) + '" class="rounded-xl border border-slate-700/70 bg-slate-950/55 px-3 py-2 text-[11px] text-slate-400 hover:bg-slate-800 hover:text-rose-300"><i class="fa-solid fa-trash mr-1"></i>删除</button>' : '') +
                (retryable ? '<button data-action="retry" data-id="' + escapeTask(task.id) + '" class="rounded-xl border border-amber-800/70 bg-amber-950/35 px-3 py-2 text-[11px] text-amber-300 hover:bg-amber-900/60"><i class="fa-solid fa-rotate-right mr-1"></i>失败重试</button>' : '') +
                '</div></div>';
            list.appendChild(card);
        });
    });

    document.querySelectorAll('[data-action="detail"]').forEach(btn => btn.onclick = () => openTaskDetail(btn.dataset.id));
    document.querySelectorAll('[data-action="run-sub"]').forEach(btn => btn.onclick = () => runSubscription(btn.dataset.id));
    document.querySelectorAll('[data-action="confirm-saved"]').forEach(btn => btn.onclick = () => resolvePendingSubscription(btn.dataset.id, 'saved'));
    document.querySelectorAll('[data-action="retry-pending"]').forEach(btn => btn.onclick = () => resolvePendingSubscription(btn.dataset.id, 'not_saved'));
    document.querySelectorAll('[data-action="retry"]').forEach(btn => btn.onclick = () => retryTask(btn.dataset.id));
    document.querySelectorAll('[data-action="delete"]').forEach(btn => btn.onclick = () => deleteTask(btn.dataset.id));
}

async function openTaskDetail(id) {
    try {
        const resp = await apiFetchTask('/api/tasks/' + encodeURIComponent(id));
        const res = await resp.json();
        if (!res.success) throw new Error(res.message || '任务不存在');
        renderTaskDetail(res.task);
        document.getElementById('task-detail-drawer').classList.remove('hidden');
    } catch (err) {
        taskToast(err.message || '无法读取任务详情');
    }
}

function closeTaskDetail() {
    document.getElementById('task-detail-drawer')?.classList.add('hidden');
}

function renderTaskProgressBar(task) {
    const phases = task.type === 'subscription'
        ? [
            ['searching', '搜刮'],
            ['resolve', '解析'],
            ['transfer', '转存'],
            ['completed', '完成']
        ]
        : [
            ['validate', '校验'],
            ['list_files', '获取文件'],
            ['create_folder', '准备目录'],
            ['transfer', '转存'],
            ['completed', '完成']
        ];
    const current = task.status === 'success' ? phases.length : phases.findIndex(p => p[0] === task.phase);
    const failed = task.status === 'failed' || task.status === 'error';
    return '<div class="mt-3 rounded-xl border border-slate-800 bg-slate-950/60 px-3 py-2.5">' +
        '<div class="flex items-center gap-1.5">' +
        phases.map((phase, index) => {
            const done = current >= 0 && index < current;
            const active = !failed && task.status === 'running' && index === current;
            const isFailed = failed && index === Math.max(0, current);
            const cls = isFailed ? 'bg-rose-500' : done ? 'bg-emerald-500' : active ? 'bg-purple-500 animate-pulse' : 'bg-slate-800';
            return '<div class="h-1.5 flex-1 rounded-full ' + cls + '" title="' + phase[1] + '"></div>';
        }).join('') +
        '</div>' +
        '<div class="mt-2 flex items-center justify-between gap-2 text-[10px]">' +
        '<span class="text-slate-400">' + escapeTask(task.phase_label || task.message || '等待执行') + '</span>' +
        '<span class="shrink-0 text-slate-600">' + (task.status === 'success' ? '已完成' : failed ? '执行失败' : task.status === 'running' ? '处理中' : '等待执行') + '</span>' +
        '</div></div>';
}

function renderSubscriptionDiagnostics(task) {
    if (task.type !== 'subscription') return '';
    const d = task.last_diagnostics;
    if (!d || typeof d !== 'object' || !Number.isFinite(Number(d.files_scanned))) {
        return '<div class="rounded-xl border border-slate-800 bg-slate-950 p-4"><div class="text-xs font-semibold text-slate-200">最近一次追剧诊断</div><div class="mt-2 text-[11px] text-slate-500">还没有可展示的结构化诊断。下次检查完成后会在这里显示资源解析、文件识别和过滤结果。</div></div>';
    }
    const metrics = [
        ['发现资源条目', d.source_candidates], ['去重后分享', d.unique_shares],
        ['成功解析分享', d.resolved_shares], ['解析失败', d.resolution_failures],
        ['频道搜索异常', d.source_search_errors], ['无效资源条目', d.invalid_source_entries],
        ['重复资源条目', d.duplicate_source_entries], ['扫描文件条目', d.files_scanned],
        ['视频文件', d.video_files], ['非视频文件', d.non_video_files],
        ['缺少文件 ID', d.missing_file_id], ['初选文件未找到', d.initial_files_missing],
        ['已保存跳过', d.already_saved], ['已追踪跳过', d.already_tracked],
        ['无法识别集数', d.unrecognized], ['起始集前过滤', d.filtered_old],
        ['跨分享同集去重', d.duplicate_episodes], ['进入转存队列', d.queued_files],
        ['已确认转存', d.transferred_files]
    ];
    const examples = Array.isArray(d.examples) ? d.examples.slice(0, 30) : [];
    const shares = Array.isArray(d.shares) ? d.shares.slice(0, 50) : [];
    const finished = d.finished_at ? new Date(Number(d.finished_at) * 1000).toLocaleString() : '未知时间';
    return '<section class="rounded-xl border border-slate-700/70 bg-slate-950 p-4">' +
        '<div class="flex flex-wrap items-center justify-between gap-2"><div class="text-xs font-semibold text-slate-200">最近一次追剧诊断</div><div class="text-[10px] text-slate-500">' + escapeTask(finished) + '</div></div>' +
        '<div class="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">' + metrics.map(([label, value]) =>
            '<div class="rounded-lg border border-slate-800 bg-slate-900/70 p-2.5"><div class="text-[10px] text-slate-500">' + label + '</div><div class="mt-1 text-lg font-semibold text-slate-100">' + Number(value || 0) + '</div></div>'
        ).join('') + '</div>' +
        '<div class="mt-3 text-[11px] ' + (d.success ? 'text-emerald-300' : 'text-amber-300') + '">' + escapeTask(d.message || '') + '</div>' +
        (shares.length ? '<details class="mt-3"><summary class="cursor-pointer text-[11px] text-slate-400">逐个分享的解析与扫描结果（最多 50 个）</summary><div class="mt-2 space-y-2">' + shares.map(item =>
            '<div class="rounded-lg border border-slate-800 px-3 py-2"><div class="flex flex-wrap items-center justify-between gap-2"><span class="break-all text-[11px] text-slate-300">' + escapeTask(item.resource_id || '未知分享') + '</span><span class="text-[10px] ' + (item.status === '已解析' ? 'text-emerald-300' : 'text-amber-300') + '">' + escapeTask(item.status || '未知状态') + '</span></div><div class="mt-1 text-[10px] text-slate-500">扫描 ' + Number(item.files_scanned || 0) + ' · 视频 ' + Number(item.video_files || 0) + ' · 入队 ' + Number(item.queued_files || 0) + ' · 跳过 ' + Number(item.skipped_files || 0) + '</div>' + (item.error ? '<div class="mt-1 break-words text-[10px] text-rose-300">' + escapeTask(item.error) + '</div>' : '') + '</div>'
        ).join('') + '</div></details>' : '') +
        (examples.length ? '<details class="mt-3"><summary class="cursor-pointer text-[11px] text-slate-400">查看跳过与异常样例（最多 30 条）</summary><div class="mt-2 space-y-2">' + examples.map(item =>
            '<div class="rounded-lg border border-slate-800 px-3 py-2"><div class="break-all text-[11px] text-slate-300">' + escapeTask(item.file || '—') + '</div><div class="mt-1 text-[10px] text-slate-500">' + escapeTask(item.reason || '') + '</div></div>'
        ).join('') + '</div></details>' : '') +
        '<div class="mt-2 text-[10px] text-slate-600">“已确认转存”仅统计接口确认成功的文件；无法识别的文件不会直接算作缺集。</div>' +
        '</section>';
}

function renderTaskDetail(task) {
    const title = document.getElementById('detail-title');
    const body = document.getElementById('detail-body');
    const state = taskState(task);
    const progress = Math.max(0, Math.min(100, Number(task.progress || 0)));
    title.textContent = task.title || '任务详情';
    const phases = task.type === 'subscription'
        ? [
            ['searching','搜索资源来源'],
            ['resolve','解析新资源'],
            ['transfer','转存新资源'],
            ['completed','检查完成']
        ]
        : [
            ['validate','校验资源'],
            ['list_files','获取文件列表'],
            ['create_folder','准备目标文件夹'],
            ['transfer','提交转存任务'],
            ['completed','转存完成']
        ];
    const phaseIndex = task.status === 'failed' ? -1 : phases.findIndex(x => x[0] === task.phase);
    body.innerHTML =
        '<div class="flex gap-4 rounded-2xl border border-slate-700/60 bg-slate-950/55 p-4 backdrop-blur-xl">' +
        (task.cover ? '<img src="/api/proxy-img?url=' + encodeURIComponent(task.cover) + '" class="h-32 w-24 shrink-0 rounded-xl object-cover bg-slate-900" onerror="this.style.display=\'none\'">' : '') +
        '<div class="min-w-0 flex-1"><div class="flex flex-wrap items-center gap-2"><span class="rounded-full border px-2 py-0.5 text-[10px] ' + state.cls + '">' + state.label + '</span><span class="text-[11px] text-slate-500">' + escapeTask(task.kind || '任务') + '</span></div>' +
        '<div class="mt-3 text-lg font-semibold text-white">' + escapeTask(task.phase_label || task.message || '等待执行') + '</div>' +
        renderTaskProgressBar(task) +
        '<div class="mt-2 text-[11px] text-slate-400">' + escapeTask(task.message || '') + '</div></div></div>' +
        '<div class="rounded-2xl border border-slate-800 bg-slate-950 p-4"><div class="text-xs font-semibold text-slate-200">执行阶段</div><div class="mt-4 space-y-3">' +
        phases.map((p,i) => {
            const done = task.status === 'success' || (phaseIndex >= 0 && i < phaseIndex);
            const active = task.status === 'running' && i === phaseIndex;
            const failed = task.status === 'failed' && i === Math.max(0, phases.findIndex(x => x[0] === task.phase));
            const cls = done ? 'text-emerald-400' : active ? 'text-purple-300' : failed ? 'text-rose-400' : 'text-slate-600';
            const icon = done ? 'fa-circle-check' : failed ? 'fa-circle-xmark' : active ? 'fa-spinner fa-spin' : 'fa-circle';
            return '<div class="flex items-center gap-3 text-xs ' + cls + '"><i class="fa-solid ' + icon + ' w-4 text-center"></i><span>' + p[1] + '</span>' + (active ? '<span class="text-[10px] text-slate-500">处理中</span>' : '') + '</div>';
        }).join('') +
        '</div></div>' +
        '<div class="grid gap-3 sm:grid-cols-2">' +
        '<div class="rounded-xl border border-slate-800 bg-slate-950 p-4"><div class="text-[10px] text-slate-500">资源信息</div><div class="mt-2 space-y-1 text-[11px] text-slate-300"><div>资源来源：' + escapeTask(task.source_channel || '—') + '</div><div>分享码：<span class="font-mono">' + escapeTask(task.share_code || '—') + '</span></div><div>文件数：' + Number(task.total || 0) + '</div></div></div>' +
        '<div class="rounded-xl border border-slate-800 bg-slate-950 p-4"><div class="text-[10px] text-slate-500">执行统计</div><div class="mt-2 space-y-1 text-[11px] text-slate-300"><div>成功：' + Number(task.success_count || 0) + '</div><div>跳过：' + Number(task.skipped_count || 0) + '</div><div>失败：' + Number(task.failed_count || 0) + '</div></div></div>' +
        '</div>' +
        '<div class="rounded-xl border border-slate-800 bg-slate-950 p-4"><div class="text-xs font-semibold text-slate-200">当前状态</div><div class="mt-2 text-[11px] leading-5 ' + (task.status === 'failed' ? 'text-rose-400' : 'text-slate-400') + '">' + escapeTask(task.message || '') + '</div></div>' +
        renderSubscriptionDiagnostics(task) +
        (task.type === 'transfer' && task.storage_target_id && (task.status === 'success' || task.recovery_uncertain) ? '<section id="task-destination-files" class="rounded-xl border border-emerald-900/60 bg-slate-950 p-4"><div class="flex flex-wrap items-center justify-between gap-2"><div><div class="text-xs font-semibold text-slate-200"><i class="fa-solid fa-folder-open mr-1 text-emerald-400"></i>目标网盘目录核对</div><div class="mt-1 text-[10px] text-slate-500">' + (task.recovery_uncertain ? '转存结果尚不确定；请先核对目标网盘，确认文件未转入前不要重复提交。' : '以下列表来自目标网盘，可用于核对转存结果。') + '</div></div><div class="flex gap-2"><button type="button" onclick="loadTaskDestinationFiles()" class="rounded-lg border border-slate-700 px-3 py-2 text-[10px] text-slate-300 hover:bg-slate-800"><i class="fa-solid fa-rotate mr-1"></i>刷新</button></div></div><div id="task-destination-files-body" class="mt-3 text-[11px] text-slate-500">正在读取网盘目录…</div></section>' : '') +
        '<details class="rounded-xl border border-slate-800 bg-slate-950 p-4"><summary class="cursor-pointer text-xs font-semibold text-slate-300">执行日志</summary><div class="mt-3 space-y-2">' +
        (task.events || []).map(e => '<div class="flex gap-3 text-[11px]"><span class="shrink-0 font-mono text-slate-600">' + new Date(Number(e.at || 0)*1000).toLocaleTimeString() + '</span><span class="' + (e.level === 'error' ? 'text-rose-400' : e.level === 'success' ? 'text-emerald-400' : 'text-slate-400') + '">' + escapeTask(e.message) + '</span></div>').join('') +
        '</div></details>' +
        (task.status === 'failed' && task.type === 'transfer' && !task.recovery_uncertain ? '<button onclick="retryTask(\'' + escapeTask(task.id) + '\'); closeTaskDetail()" class="w-full rounded-xl bg-amber-600 px-4 py-3 text-xs font-medium text-white hover:bg-amber-500"><i class="fa-solid fa-rotate-right mr-1"></i>失败重试</button>' : '');
    if (task.type === 'transfer' && task.storage_target_id && (task.status === 'success' || task.recovery_uncertain)) {
        window.currentDestinationTask = {targetId: task.storage_target_id, parentFid: task.target_fid || '0'};
        loadTaskDestinationFiles();
    }
}


async function loadTaskDestinationFiles(attempt = 0, refreshSequence = null) {
    const task = window.currentDestinationTask;
    const section = document.getElementById('task-destination-files');
    const body = document.getElementById('task-destination-files-body');
    if (!task || !section || !body) return;

    if (attempt === 0) {
        window.taskDestinationRefreshSequence = (window.taskDestinationRefreshSequence || 0) + 1;
        refreshSequence = window.taskDestinationRefreshSequence;
        if (window.taskDestinationRefreshTimer) {
            clearTimeout(window.taskDestinationRefreshTimer);
            window.taskDestinationRefreshTimer = null;
        }
        body.innerHTML = '<div class="py-3 text-slate-500"><i class="fa-solid fa-spinner fa-spin mr-2"></i>正在读取目标网盘目录及子目录…</div>';
    } else if (refreshSequence !== window.taskDestinationRefreshSequence) {
        return;
    }

    const scheduleRetry = () => {
        if (attempt >= 2 || refreshSequence !== window.taskDestinationRefreshSequence) return;
        window.taskDestinationRefreshTimer = setTimeout(
            () => loadTaskDestinationFiles(attempt + 1, refreshSequence),
            1000 * (attempt + 1)
        );
    };
    try {
        // Read the capability from card manifests. The UI stays provider-agnostic;
        // only cards declaring storage.list_destination_files get this feature.
        const cardsResponse = await apiFetchTask('/api/cards');
        const cardsResult = await cardsResponse.json();
        if (cardsResponse.ok && cardsResult.success && Array.isArray(cardsResult.cards)) {
            const card = cardsResult.cards.find(item => item && item.id === task.targetId);
            if (!card || !Array.isArray(card.capabilities) || !card.capabilities.includes('storage.list_destination_files')) {
                body.innerHTML = '<div class="py-3 text-amber-200">此存储卡片暂不支持在 MovieSync 内读取网盘目录。请打开对应网盘客户端核对；如果转存结果待核实，确认文件未转入前不要重复提交。</div>';
                const refreshButton = section.querySelector('button');
                if (refreshButton) refreshButton.disabled = true;
                return;
            }
        }

        const maxDepth = 4;
        const maxItems = 400;
        let itemCount = 0;
        let truncated = false;

        async function readDirectory(parentFid, depth) {
            const query = new URLSearchParams({parent_fid: String(parentFid || '0')});
            const response = await apiFetchTask('/api/storage-targets/' + encodeURIComponent(task.targetId) + '/files?' + query.toString());
            const result = await response.json();
            if (!response.ok || !result.success) throw new Error(result.message || '读取网盘目录失败');
            const files = Array.isArray(result.files) ? result.files.slice() : [];
            files.sort((a, b) => {
                const dirOrder = Number(Boolean(b.is_dir)) - Number(Boolean(a.is_dir));
                return dirOrder || String(a.file_name || '').localeCompare(String(b.file_name || ''), 'zh-CN');
            });

            let html = '';
            for (const file of files) {
                if (itemCount >= maxItems) {
                    truncated = true;
                    break;
                }
                itemCount += 1;
                const name = escapeTask(file.file_name || '未命名项目');
                const isDir = Boolean(file.is_dir);
                let childHtml = '';
                let childCount = 0;
                if (isDir && file.fid && depth < maxDepth && itemCount < maxItems) {
                    const children = await readDirectory(file.fid, depth + 1);
                    childHtml = children.html;
                    childCount = children.count;
                } else if (isDir && depth >= maxDepth) {
                    truncated = true;
                }
                const icon = isDir ? 'fa-folder text-amber-400' : (file.is_video ? 'fa-file-video text-emerald-400' : 'fa-file text-slate-400');
                const detail = isDir
                    ? (depth >= maxDepth ? '子目录层级较深，未继续展开' : (childCount ? '文件夹 · ' + childCount + ' 项' : '空文件夹'))
                    : formatTaskFileSize(file.size);
                html += '<div class="flex items-start gap-3 rounded-lg border border-slate-800/70 bg-slate-900/50 px-3 py-2" style="margin-left:' + (depth * 18) + 'px">' +
                    '<i class="fa-solid ' + icon + ' mt-0.5 w-4 shrink-0"></i>' +
                    '<span class="min-w-0 flex-1 break-all text-slate-200">' + name + '</span>' +
                    '<span class="shrink-0 text-right text-[10px] text-slate-500">' + detail + '</span></div>' + childHtml;
            }
            return {html: html, count: files.length};
        }

        const tree = await readDirectory(task.parentFid || '0', 0);
        const rootLabel = '<div class="mb-2 flex flex-wrap items-center justify-between gap-2 text-[10px] text-slate-400"><span><i class="fa-solid fa-folder-tree mr-1 text-amber-400"></i>转存主目录</span><span>目录 FID：' + escapeTask(task.parentFid || '0') + ' · ' + itemCount + ' 项</span></div>';
        if (!tree.html) {
            body.innerHTML = rootLabel + '<div class="rounded-lg border border-dashed border-slate-800 px-3 py-5 text-center text-slate-500">主目录暂时没有显示文件，MovieSync 会自动再次刷新核对。</div>';
            scheduleRetry();
            return;
        }
        body.innerHTML = rootLabel + '<div class="max-h-[28rem] space-y-1 overflow-y-auto">' + tree.html + '</div>' +
            (truncated ? '<div class="mt-2 text-[10px] text-amber-400">为避免目录过大影响页面，列表最多展开 4 层、显示 400 项；更深或更多内容请打开夸克查看。</div>' : '') +
            '<div class="mt-2 text-[10px] text-slate-500">文件夹已按层级缩进显示；文件大小仅对文件展示。MovieSync 会在转存后自动复核几次，亦可手动刷新。</div>';
        scheduleRetry();
    } catch (error) {
        body.innerHTML = '<div class="rounded-lg border border-rose-900/50 bg-rose-950/20 px-3 py-4 text-rose-300">' + escapeTask(error.message || '读取网盘目录失败') + '<div class="mt-1 text-[10px] text-rose-400/80">转存任务状态不受影响，MovieSync 会自动重试刷新目录。</div></div>';
        scheduleRetry();
    }
}

function formatTaskFileSize(value) {
    const size = Number(value || 0);
    if (!Number.isFinite(size) || size <= 0) return '大小未知';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let amount = size, unit = 0;
    while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit++; }
    return amount.toFixed(unit === 0 ? 0 : 1) + ' ' + units[unit];
}

function renderHistory() {
    const box = document.getElementById('history-list');
    if (!box) return;
    const rows = [];
    tasks.forEach(task => {
        (task.run_history || []).forEach(item => rows.push({
            ...item,
            title: task.title,
            kind: task.kind || '任务'
        }));
    });
    tasks.filter(t => t.type === 'transfer').forEach(task => {
        rows.push({
            at: task.updated_at || task.created_at,
            success: task.status === 'success',
            message: task.message || '',
            success_count: task.success_count || 0,
            failed_count: task.failed_count || 0,
            title: task.title,
            kind: task.kind || '普通转存'
        });
    });
    rows.sort((a,b) => Number(b.at || 0) - Number(a.at || 0));
    const recent = rows.slice(0, 20);
    if (!recent.length) {
        box.innerHTML = '<div class="p-10 text-center text-xs text-slate-500">暂无执行记录。</div>';
        return;
    }
    box.innerHTML = recent.map(item => {
        const time = item.at ? new Date(Number(item.at) * 1000).toLocaleString() : '未知时间';
        const cls = item.success ? 'text-emerald-400' : 'text-rose-400';
        const icon = item.success ? 'fa-circle-check' : 'fa-circle-xmark';
        return '<div class="flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:justify-between">' +
            '<div class="min-w-0"><div class="flex flex-wrap items-center gap-2"><i class="fa-solid ' + icon + ' ' + cls + '"></i>' +
            '<span class="text-xs font-medium text-slate-200">' + escapeTask(item.title) + '</span>' +
            '<span class="text-[10px] text-slate-500">' + escapeTask(item.kind) + '</span></div>' +
            '<div class="mt-1 text-[11px] text-slate-500">' + escapeTask(item.message) + '</div></div>' +
            '<div class="shrink-0 text-[10px] text-slate-600">' + escapeTask(time) + '</div></div>';
    }).join('');
}

function openAddTask() {
    candidateSearchSequence++;
    selectedCandidate = null;
    document.getElementById('task-submit').disabled = true;
    document.getElementById('task-submit').classList.add('opacity-40');
    document.getElementById('task-form-status').textContent = '';
    document.getElementById('task-candidates').classList.add('hidden');
    document.getElementById('task-candidate-list').innerHTML = '';
    document.getElementById('task-drawer').classList.remove('hidden');
    loadTaskCategories();
    const params = new URLSearchParams(window.location.search);
    const presetTitle = params.get('title') || '';
    if (presetTitle) document.getElementById('task-title').value = presetTitle;
    setTimeout(() => document.getElementById('task-title').focus(), 50);
}

function closeAddTask() {
    document.getElementById('task-drawer').classList.add('hidden');
}

function formatTaskSize(bytes) {
    const n = Number(bytes || 0);
    if (!n) return '未知大小';
    const units = ['B','KB','MB','GB','TB'];
    let value = n, index = 0;
    while (value >= 1024 && index < units.length - 1) { value /= 1024; index++; }
    return (value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)) + ' ' + units[index];
}

async function searchTaskCandidates() {
    const requestId = ++candidateSearchSequence;
    const title = document.getElementById('task-title').value.trim();
    if (!title) return taskToast('请输入剧名');
    const box = document.getElementById('task-candidate-list');
    document.getElementById('task-candidates').classList.remove('hidden');
    box.innerHTML = '<div class="rounded-xl border border-slate-800 bg-slate-950 p-6 text-center text-xs text-slate-500"><i class="fa-solid fa-spinner fa-spin mr-1"></i>正在同时搜索多个资源来源…</div>';
    selectedCandidate = null;
    document.getElementById('task-submit').disabled = true;
    document.getElementById('task-submit').classList.add('opacity-40');
    try {
        const resp = await apiFetchTask('/api/search-candidates', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({movies:[{title:title,tag:'电视剧'}]})});
        const res = await resp.json();
        if (requestId !== candidateSearchSequence) return;
        if (!res.success) throw new Error(res.message || '搜索失败');
        const candidates = (res.candidates_map || {})[title] || [];
        if (!candidates.length) {
            box.innerHTML = '<div class="rounded-xl border border-amber-900/50 bg-amber-950/20 p-5 text-center text-xs text-amber-300">没有找到匹配资源，请换个剧名，或检查已启用的资源来源。</div>';
            return;
        }
        const initiallyVisibleCandidates = 5;
        document.getElementById('candidate-hint').textContent =
            '找到 ' + candidates.length + ' 个可用源；默认展示前 ' +
            Math.min(candidates.length, initiallyVisibleCandidates) +
            ' 个，其余可展开查看（按搜索顺序展示，不代表推荐排名）';
        box.innerHTML = '';

        const candidateRows = document.createElement('div');
        candidateRows.className = 'space-y-3';
        if (candidates.length > initiallyVisibleCandidates) {
            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'w-full rounded-xl border border-slate-700 bg-slate-900/70 px-4 py-3 text-xs font-medium text-slate-300 transition hover:border-purple-700 hover:text-white';
            toggle.setAttribute('aria-expanded', 'false');
            toggle.textContent = '展开其余 ' + (candidates.length - initiallyVisibleCandidates) + ' 个资源';
            toggle.onclick = () => {
                const expanded = toggle.getAttribute('aria-expanded') === 'true';
                candidateRows.querySelectorAll('.smart-follow-candidate-extra').forEach(row => {
                    row.classList.toggle('hidden', expanded);
                });
                toggle.setAttribute('aria-expanded', String(!expanded));
                toggle.textContent = expanded
                    ? '展开其余 ' + (candidates.length - initiallyVisibleCandidates) + ' 个资源'
                    : '收起其余资源';
            };
            box.appendChild(toggle);
        }

        candidates.forEach((candidate, index) => {
            const row = document.createElement('div');
            const files = Array.isArray(candidate.files) ? candidate.files : [];
            const resolutions = Object.keys(candidate.resolutions || {});
            const resolutionText = resolutions.length ? resolutions.join(' / ') : [...new Set(files.map(f => f.resolution).filter(Boolean))].join(' / ') || '未知';
            const totalSize = candidate.total_size_text || formatTaskSize(files.reduce((sum, f) => sum + Number(f.size || 0), 0));
            const validFids = files.map(file => String(file.fid || '').trim()).filter(Boolean);
            const selectedFids = new Set(validFids);
            row.className = 'rounded-2xl border border-slate-800 bg-slate-950/80 p-4 transition hover:border-slate-700';
            if (index >= initiallyVisibleCandidates) {
                row.classList.add('smart-follow-candidate-extra', 'hidden');
            }
            row.innerHTML =
                '<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div class="min-w-0">' +
                '<div class="flex flex-wrap items-center gap-2"><span class="inline-flex items-center gap-1.5 rounded-lg border border-sky-900/60 bg-sky-950/40 px-2 py-1 text-[11px] font-semibold text-sky-300"><i class="fa-solid fa-puzzle-piece"></i>' + escapeTask(candidate.source_name || candidate.channel || '资源来源') + '</span>' +
                '<span class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-[10px] text-slate-500">' + escapeTask(candidate.resource_id || candidate.share_id || candidate.pwd_id || '') + '</span></div>' +
                '<div class="mt-3 flex flex-wrap gap-2"><span class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] text-slate-400"><i class="fa-solid fa-film mr-1"></i>' + files.length + ' 个视频</span>' +
                '<span class="rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] text-slate-400"><i class="fa-solid fa-hard-drive mr-1"></i>' + escapeTask(totalSize) + '</span>' +
                '<span class="rounded-lg border border-blue-900/60 bg-blue-950/30 px-2 py-1 text-[10px] text-blue-300"><i class="fa-solid fa-display mr-1"></i>' + escapeTask(resolutionText) + '</span></div></div>' +
                '<button data-candidate="' + index + '" class="candidate-select shrink-0 rounded-xl border border-purple-700 bg-purple-950/40 px-4 py-2.5 text-[11px] font-medium text-purple-200 hover:bg-purple-900/50">选择此源</button></div>' +
                '<div class="mt-2 flex items-center justify-between rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-2">' +
                '<span class="text-[11px] text-slate-400">已选 <strong data-selected-count class="text-slate-200">' + validFids.length + '</strong> / ' + validFids.length + ' 集</span>' +
                '<button type="button" data-toggle-files class="text-[11px] text-purple-300 hover:text-purple-200">' + (validFids.length ? '取消全选' : '全选') + '</button>' +
                '</div>' +
                '<div class="mt-2 max-h-60 overflow-y-auto rounded-xl border border-slate-800 bg-slate-900/80 p-2 space-y-1">' +
                files.map((file, fileIndex) => {
                    const fid = String(file.fid || '').trim();
                    if (!fid) return '';
                    return '<label class="flex cursor-pointer items-center gap-3 rounded-lg px-2 py-2 hover:bg-slate-800/70"><input type="checkbox" data-file-index="' + fileIndex + '" class="task-episode-checkbox h-4 w-4 rounded border-slate-600 bg-slate-950 text-purple-600 focus:ring-purple-500" checked><span class="min-w-0 flex-1 truncate text-xs text-slate-300">' + escapeTask(file.file_name) + '</span><span class="shrink-0 text-[10px] text-slate-500">' + escapeTask(file.resolution || '未知') + ' · ' + escapeTask(file.size_text || formatTaskSize(file.size)) + '</span></label>';
                }).join('') +
                '</div>' +
                '<div class="mt-2 text-[10px] text-slate-500">默认选择当前已发现的全部集数；取消某些集只影响首次处理，后续新集仍会自动追更。</div>';
            const updateSelectionUi = () => {
                const checked = [...row.querySelectorAll('.task-episode-checkbox:checked')];
                const selected = new Set(checked.map(input => {
                    const file = files[Number(input.dataset.fileIndex)];
                    return String(file?.fid || '').trim();
                }).filter(Boolean));
                row.querySelector('[data-selected-count]').textContent = selected.size;
                row.querySelector('[data-toggle-files]').textContent = selected.size === validFids.length ? '取消全选' : '全选';
                if (selectedCandidate?.row === row) {
                    selectedCandidate.selectedFids = selected;
                    refreshTaskSubmitState();
                    const enabled = selected.size > 0;
                    document.getElementById('task-form-status').textContent = enabled
                        ? '已选择 ' + selected.size + ' 集；后续有新集时，系统会继续检查并自动转存。'
                        : '至少选择 1 集作为首次处理基线。';
                }
            };
            row.querySelectorAll('.task-episode-checkbox').forEach(input => input.addEventListener('change', updateSelectionUi));
            row.querySelector('[data-toggle-files]').onclick = () => {
                const shouldSelectAll = row.querySelectorAll('.task-episode-checkbox:checked').length !== validFids.length;
                row.querySelectorAll('.task-episode-checkbox').forEach(input => { input.checked = shouldSelectAll; });
                updateSelectionUi();
            };
            row.querySelector('.candidate-select').onclick = function() {
                const currentSelected = new Set([...row.querySelectorAll('.task-episode-checkbox:checked')].map(input => {
                    const file = files[Number(input.dataset.fileIndex)];
                    return String(file?.fid || '').trim();
                }).filter(Boolean));
                selectedCandidate = {candidate:candidate,row:row,selectedFids:currentSelected};
                const hasCompatibleTargets = filterStorageTargetsForCandidate(candidate);
                document.querySelectorAll('.candidate-select').forEach(b => { b.className='candidate-select shrink-0 rounded-xl border border-slate-700 bg-slate-900 px-4 py-2.5 text-[11px] text-slate-400'; b.textContent='选择此源'; });
                this.className='candidate-select shrink-0 rounded-xl bg-emerald-600 px-4 py-2.5 text-[11px] font-medium text-white';
                this.textContent='已选择';
                refreshTaskSubmitState();
                if (!hasCompatibleTargets) return;
                document.getElementById('task-form-status').textContent = selectedFids.size
                    ? '已选择 ' + selectedFids.size + ' 集；后续有新集时，系统会继续检查并自动转存。'
                    : '至少选择 1 集作为首次处理基线。';
            };
            candidateRows.appendChild(row);
        });
        box.appendChild(candidateRows);

    } catch (err) {
        if (requestId === candidateSearchSequence) {
            box.innerHTML = '<div class="rounded-xl border border-rose-900/50 bg-rose-950/20 p-5 text-xs text-rose-300">搜索失败：' + escapeTask(err.message) + '</div>';
        }
    }
}

async function createTask() {
    if (!selectedCandidate) return taskToast('请先选择一个资源来源');
    const title = document.getElementById('task-title').value.trim();
    const selectedFids = selectedCandidate.selectedFids || new Set();
    const files = (selectedCandidate.candidate.files || [])
        .filter(x => selectedFids.has(String(x.fid || '').trim()))
        .map(x => ({fid:x.fid, file_name:x.file_name || ''}))
        .filter(x => x.fid);
    if (!files.length) return taskToast('请至少选择 1 集作为首次处理基线');
    const storageTargetId = document.getElementById('task-storage-target')?.value;
    const targetFid = document.getElementById('task-target-fid')?.value;
    if (!storageTargetId || !targetFid
        || document.getElementById('task-storage-target')?.disabled
        || document.getElementById('task-target-fid')?.disabled) {
        return taskToast('请先选择可用的转存位置和保存目录，并等待目录加载完成');
    }
    const payload = {
        title:title,
        channel:selectedCandidate.candidate.channel_id || selectedCandidate.candidate.channel || '',
        channel_name:selectedCandidate.candidate.channel || '',
        source_id:selectedCandidate.candidate.source_id || '',
        storage_target_id:document.getElementById('task-storage-target').value,
        pwd_id:selectedCandidate.candidate.pwd_id || '',
        resource_id:selectedCandidate.candidate.resource_id || selectedCandidate.candidate.share_id || selectedCandidate.candidate.pwd_id || '',
        resource_type:selectedCandidate.candidate.resource_type || '',
        files:files,
        cover:selectedCandidate.candidate.cover || '',
        interval_hours:Number(document.getElementById('task-interval').value || 6),
        target_fid:targetFid
    };
    const btn=document.getElementById('task-submit');
    btn.disabled=true;
    document.getElementById('task-form-status').textContent='正在保存…';
    try {
        const resp=await apiFetchTask('/api/subscriptions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        const res=await resp.json();
        if(!res.success) throw new Error(res.message || '保存失败');
        taskToast('追剧任务已创建');
        closeAddTask();
        await loadTasks();
    }catch(err){
        taskToast(err.message || '保存失败');
        refreshTaskSubmitState();
    }
}

async function resolvePendingSubscription(id, action) {
    const message = action === 'saved'
        ? '请先确认目标网盘中已存在这些文件。确认后系统会将其记为已转存，不再重复提交。继续吗？'
        : '请先确认目标网盘中确实没有这些文件。系统将重新检查并可能再次提交转存。继续吗？';
    if (!confirm(message)) return;
    try {
        const resp = await apiFetchTask('/api/subscriptions/resolve-pending', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id, action})
        });
        const res = await resp.json();
        if (!res.success) throw new Error(res.message || '处理待确认转存失败');
        taskToast(res.message || '待确认转存已处理');
    } catch (err) {
        taskToast(err.message || '处理待确认转存失败');
    } finally {
        await loadTasks();
    }
}

async function runSubscription(id) {
    if (!taskRefreshTimer) {
        taskRefreshTimer = setInterval(() => loadTasks(), 2000);
    }
    try {
        const resp = await apiFetchTask('/api/subscriptions/run-now', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id})});
        const res = await resp.json();
        taskToast(res.message || (res.success ? '检查完成' : '检查失败'));
    } catch (err) {
        taskToast('手动检查失败');
    } finally {
        await loadTasks();
    }
}

async function retryTask(id) {
    try {
        const resp = await apiFetchTask('/api/tasks/' + encodeURIComponent(id) + '/retry', {method:'POST'});
        const res = await resp.json();
        if (!res.success) throw new Error(res.message || '重试失败');
        taskToast('已创建重试任务');
        await loadTasks();
    } catch (err) {
        taskToast(err.message || '重试失败');
    }
}

async function deleteTask(id) {
    const task=tasks.find(x=>String(x.id)===String(id));
    if(!task) return;
    if(!confirm('确定删除“' + task.title + '”吗？删除后不会影响已经保存到网盘的文件。')) return;
    try {
        const resp=await apiFetchTask('/api/tasks/' + encodeURIComponent(id),{method:'DELETE'});
        const res=await resp.json();
        if(!res.success) throw new Error(res.message || '删除失败');
        taskToast('任务已删除');
        await loadTasks();
    }catch(err){ taskToast(err.message || '删除失败'); }
}

async function clearHistory() {
    const terminal = tasks.filter(t => ['success','failed','error'].includes(t.status));
    if (!terminal.length) return taskToast('没有可清理的已结束任务');
    if (!confirm('确定清空全部已完成/失败任务及执行记录吗？正在执行中的任务不会受到影响。')) return;
    try {
        const resp=await apiFetchTask('/api/tasks/clear-history',{method:'POST'});
        const res=await resp.json();
        if(!res.success) throw new Error(res.message || '清理失败');
        taskToast(res.message || '历史已清空');
        await loadTasks();
    }catch(err){ taskToast(err.message || '清理失败'); }
}

setInterval(() => { if (!document.hidden && tasks.length) { renderStats(); renderTasks(); } }, 60000);
