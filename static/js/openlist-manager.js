/* OpenList mount manager UI. Driver schema and storage operations are proxied by MovieSync. */
(() => {
    let editorState = null;

    async function api(url, options = {}) {
        const response = await apiFetch(url, options);
        const result = await response.json();
        if (!response.ok || !result.success) throw new Error(result.message || "OpenList 请求失败");
        return result;
    }

    function showMessage(message, isError = false, target = "openlist-mount-message") {
        const node = document.getElementById(target);
        if (!node) return;
        node.className = "rounded-lg border p-3 text-xs " + (isError
            ? "border-rose-700/60 bg-rose-950/30 text-rose-200"
            : "border-emerald-700/60 bg-emerald-950/20 text-emerald-200");
        node.textContent = message;
        node.classList.remove("hidden");
    }

    function hideMessage(target) {
        document.getElementById(target)?.classList.add("hidden");
    }

    window.loadOpenListMounts = async function () {
        const list = document.getElementById("openlist-mount-list");
        if (!list) return;
        list.innerHTML = '<div class="rounded-lg border border-slate-800 p-3 text-xs text-slate-400">正在读取挂载列表…</div>';
        hideMessage("openlist-mount-message");
        try {
            const result = await api("/api/openlist/storages");
            const data = result.storages || {};
            const items = Array.isArray(data.content) ? data.content : (Array.isArray(data) ? data : []);
            if (!items.length) {
                list.innerHTML = '<div class="rounded-lg border border-slate-800 p-4 text-xs text-slate-500">当前没有挂载。点击“新增挂载”，选择网盘驱动并填写动态参数即可。</div>';
                return;
            }
            list.innerHTML = items.map(item => {
                const disabled = Boolean(item.disabled);
                const mountPath = escapeHtml(item.mount_path || "(未命名挂载)");
                const driver = escapeHtml(item.driver || "未知驱动");
                const remark = escapeHtml(item.remark || "");
                const status = disabled
                    ? '<span class="rounded bg-slate-800 px-2 py-1 text-[10px] text-slate-400">已停用</span>'
                    : '<span class="rounded bg-emerald-950/60 px-2 py-1 text-[10px] text-emerald-300">已启用</span>';
                return '<div class="flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/50 p-3 sm:flex-row sm:items-center sm:justify-between">' +
                    '<div class="min-w-0"><div class="flex flex-wrap items-center gap-2"><span class="break-all text-sm font-semibold text-slate-100">' + mountPath + '</span>' + status + '</div>' +
                    '<div class="mt-1 text-[11px] text-slate-400">' + driver + (remark ? ' · ' + remark : '') + '</div></div>' +
                    '<div class="flex flex-wrap gap-2">' +
                    '<button type="button" data-action="edit" data-id="' + Number(item.id || 0) + '" class="rounded-lg bg-slate-800 px-3 py-2 text-[11px] text-slate-200">编辑</button>' +
                    '<button type="button" data-action="toggle" data-id="' + Number(item.id || 0) + '" data-enabled="' + (!disabled) + '" class="rounded-lg bg-slate-800 px-3 py-2 text-[11px] text-slate-200">' + (disabled ? "启用" : "停用") + '</button>' +
                    '<button type="button" data-action="delete" data-id="' + Number(item.id || 0) + '" class="rounded-lg bg-rose-950/60 px-3 py-2 text-[11px] text-rose-200">删除</button>' +
                    '</div></div>';
            }).join("");

            list.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", async () => {
                const id = Number(button.dataset.id || 0);
                const item = items.find(entry => Number(entry.id) === id);
                try {
                    if (button.dataset.action === "edit") {
                        await window.openOpenListMountEditor(item);
                    } else if (button.dataset.action === "toggle") {
                        const wasEnabled = button.dataset.enabled === "true";
                        await api("/api/openlist/storages/" + id + "/enabled", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({enabled: !wasEnabled})
                        });
                        await window.loadOpenListMounts();
                        showMessage((wasEnabled ? "已停用：" : "已启用：") + (item?.mount_path || id));
                    } else if (button.dataset.action === "delete") {
                        if (!confirm("确定删除挂载“" + (item?.mount_path || id) + "”吗？这会从 OpenList 中删除挂载配置。")) return;
                        await api("/api/openlist/storages/" + id, {method: "DELETE"});
                        await window.loadOpenListMounts();
                        showMessage("挂载已删除。");
                    }
                } catch (error) {
                    showMessage(error.message || "挂载操作失败", true);
                }
            }));
        } catch (error) {
            list.innerHTML = '<div class="rounded-lg border border-rose-800/60 bg-rose-950/20 p-3 text-xs text-rose-200">' + escapeHtml(error.message || "读取挂载列表失败") + '</div>';
        }
    };

    window.openOpenListMountEditor = async function (storage = null) {
        try {
            const result = await api("/api/openlist/drivers");
            const names = Array.isArray(result.names) ? result.names : Object.keys(result.drivers || {});
            if (!names.length) throw new Error("OpenList 没有返回可用驱动列表");
            editorState = {storage, names};
            const select = document.getElementById("openlist-mount-driver");
            select.replaceChildren(...names.sort((a, b) => a.localeCompare(b)).map(name => new Option(name, name)));
            select.value = storage?.driver && names.includes(storage.driver) ? storage.driver : names[0];
            document.getElementById("openlist-mount-editor-title").textContent = storage
                ? "编辑挂载 · " + (storage.mount_path || storage.id)
                : "新增 OpenList 挂载";
            hideMessage("openlist-mount-editor-message");
            select.onchange = () => renderFields(select.value).catch(error =>
                showMessage(error.message || "读取驱动参数失败", true, "openlist-mount-editor-message"));
            await renderFields(select.value);
            document.getElementById("openlist-mount-editor").classList.remove("hidden");
        } catch (error) {
            showMessage(error.message || "无法加载驱动配置", true);
        }
    };

    window.closeOpenListMountEditor = function () {
        document.getElementById("openlist-mount-editor").classList.add("hidden");
        editorState = null;
    };

    function fieldValue(field, value) {
        if (value !== undefined && value !== null && value !== "") return value;
        return field.default !== undefined ? field.default : "";
    }

    function renderField(field, value, scope) {
        const key = String(field.name || "");
        const id = "ol-" + scope + "-" + key.replace(/[^a-zA-Z0-9_-]/g, "-");
        const label = escapeHtml(key.replaceAll("_", " "));
        const help = field.help
            ? '<p class="mt-1 text-[10px] leading-4 text-slate-500">' + escapeHtml(field.help) + '</p>'
            : "";
        const type = String(field.type || "string").toLowerCase();
        const required = field.required ? " required" : "";
        let control;
        if (type === "bool" || type === "boolean") {
            const checked = value === true || String(value).toLowerCase() === "true";
            control = '<label class="flex min-h-10 items-center gap-2 rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 text-xs text-slate-200"><input id="' + id + '" type="checkbox" data-scope="' + scope + '" data-field="' + escapeHtml(key) + '" class="h-4 w-4 accent-sky-500"' + (checked ? " checked" : "") + '>启用</label>';
        } else if (type === "select") {
            const options = String(field.options || "").split(",").map(x => x.trim()).filter(Boolean);
            control = '<select id="' + id + '" data-scope="' + scope + '" data-field="' + escapeHtml(key) + '" class="glass-input w-full rounded-lg border px-3 py-2 text-sm"' + required + '>' +
                options.map(option => '<option value="' + escapeHtml(option) + '"' + (String(value) === option ? " selected" : "") + '>' + escapeHtml(option) + '</option>').join("") + '</select>';
        } else {
            const number = ["number", "int", "int8", "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32", "uint64", "float32", "float64"].includes(type);
            control = '<input id="' + id + '" type="' + (number ? "number" : "text") + '" data-scope="' + scope + '" data-field="' + escapeHtml(key) + '" value="' + escapeHtml(value) + '" class="glass-input w-full rounded-lg border px-3 py-2 text-sm"' + (number ? ' step="any"' : "") + required + '>';
        }
        return '<div class="min-w-0"><label for="' + id + '" class="mb-1 block text-xs font-semibold text-slate-300">' + label + (field.required ? ' <span class="text-rose-300">*</span>' : '') + '</label>' + control + help + '</div>';
    }

    async function renderFields(driverName) {
        if (!editorState) return;
        const result = await api("/api/openlist/drivers/" + encodeURIComponent(driverName));
        const info = result.fields || {};
        const storage = editorState.storage;
        let addition = {};
        if (storage?.addition) {
            try {
                addition = typeof storage.addition === "string" ? JSON.parse(storage.addition || "{}") : storage.addition;
            } catch (_) {
                addition = {};
            }
        }
        const common = Array.isArray(info.common) ? info.common : [];
        const additional = Array.isArray(info.additional) ? info.additional : [];
        const commonHtml = common.map(field => {
            let value = storage ? storage[field.name] : undefined;
            if (field.name === "driver") value = driverName;
            return renderField(field, fieldValue(field, value), "common");
        }).join("");
        const additionalHtml = additional.map(field =>
            renderField(field, fieldValue(field, addition[field.name]), "additional")
        ).join("");
        document.getElementById("openlist-mount-fields").innerHTML =
            '<div class="md:col-span-2"><h4 class="border-b border-slate-800 pb-2 text-xs font-semibold text-sky-200">通用挂载设置</h4></div>' +
            commonHtml + '<div class="md:col-span-2 mt-2"><h4 class="border-b border-slate-800 pb-2 text-xs font-semibold text-sky-200">' + escapeHtml(driverName) + ' 驱动参数</h4></div>' +
            (additionalHtml || '<p class="text-xs text-slate-500">此驱动没有额外参数。</p>');
        document.getElementById("openlist-mount-save").disabled = false;
    }

    window.saveOpenListMount = async function () {
        if (!editorState) return;
        const button = document.getElementById("openlist-mount-save");
        button.disabled = true;
        try {
            const payload = {driver: document.getElementById("openlist-mount-driver").value};
            const addition = {};
            document.querySelectorAll("#openlist-mount-fields [data-field]").forEach(control => {
                const key = control.dataset.field;
                const scope = control.dataset.scope;
                let value = control.type === "checkbox" ? control.checked : control.value.trim();
                if (control.required && value === "") throw new Error("请填写必填项：" + key);
                if (value !== "" && control.type === "number") {
                    value = Number(value);
                    if (!Number.isFinite(value)) throw new Error("字段 " + key + " 必须是数字");
                }
                if (scope === "additional") addition[key] = value;
                else payload[key] = value;
            });
            payload.addition = JSON.stringify(addition);
            const storage = editorState.storage;
            if (storage?.id) {
                payload.id = storage.id;
                await api("/api/openlist/storages", {
                    method: "PUT",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload)
                });
            } else {
                await api("/api/openlist/storages", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload)
                });
            }
            window.closeOpenListMountEditor();
            await window.loadOpenListMounts();
            showMessage(storage ? "挂载配置已更新。" : "挂载已创建。");
        } catch (error) {
            showMessage(error.message || "保存挂载失败", true, "openlist-mount-editor-message");
        } finally {
            button.disabled = false;
        }
    };
})();