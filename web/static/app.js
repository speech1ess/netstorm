// --- 1. ГЛОБАЛЬНОЕ СОСТОЯНИЕ (STATE) ---
let globalMenu = [];
let currentTab = 'tests';
let pendingPayload = null;
let isHandlingPause = false;

// --- 2. API ВЫЗОВЫ (NETWORK) ---
async function loadConfigSelector() {
    try {
        const response = await fetch('/api/configs');
        const data = await response.json();
        const selectElement = document.getElementById('pmi-select');
        selectElement.innerHTML = ''; 

        data.available_configs.forEach(configName => {
            const option = document.createElement('option');
            option.value = configName;
            option.textContent = configName;
            if (configName === data.active_config) option.selected = true;
            selectElement.appendChild(option);
        });
        selectElement.onchange = (e) => switchPMI(e.target.value);
    } catch (error) { console.error("Ошибка загрузки конфигов:", error); }
}

async function switchPMI(filename) {
    if (!filename) return;
    try {
        const response = await fetch('/api/configs/active', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: filename })
        });
        if (response.ok) {
            await loadMainMenu(); 
            ConfigTab.syncWithMain(); 
        }
    } catch (error) { console.error("Ошибка переключения ПМИ:", error); }
}

async function loadMainMenu() {
    try {
        const r = await fetch('/api/menu');
        const menu = await r.json();
        if (menu.length === 0) return;

        const cleanMenuStr = JSON.stringify(menu).replace(/🦖 /g, '');
        globalMenu = JSON.parse(cleanMenuStr);
        switchTab(currentTab);
    } catch (e) { console.error("Ошибка загрузки меню:", e); }
}

async function updateStatus() {
    try {
        const r = await fetch('/api/status');
        const d = await r.json();
        document.getElementById('st-line').innerText = d.res;
        
        const statusBadge = document.getElementById('run-status');
        const killBtn = document.getElementById('kill-btn');
        
        if (d.is_running) {
            statusBadge.className = "text-[10px] font-bold uppercase tracking-widest px-3 py-1.5 rounded bg-red-900/20 text-red-400 border border-red-900/50 hidden md:flex items-center gap-2 cursor-pointer shadow-[0_0_10px_rgba(239,68,68,0.2)]";
            statusBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-red-500 animate-pulse shadow-[0_0_8px_rgba(239,68,68,1)]"></span> RUNNING';
            killBtn.classList.remove('hidden');
            killBtn.classList.add('flex');
        } else {
            statusBadge.className = "text-[10px] font-bold uppercase tracking-widest px-3 py-1.5 rounded bg-zinc-800 text-zinc-500 border border-zinc-700 hidden md:flex items-center gap-2 cursor-pointer";
            statusBadge.innerHTML = '<span class="w-2 h-2 rounded-full bg-zinc-500"></span> IDLE';
            killBtn.classList.add('hidden');
            killBtn.classList.remove('flex');
        }
    } catch(e) {}
}

// --- 3. UI И РЕНДЕРИНГ (DOM MANIPULATION) ---
function switchTab(tabId) {
    currentTab = tabId;
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('opacity-100', 'text-white', 'border-b-2', 'border-green-500');
        btn.classList.add('opacity-60');
    });
    const activeBtn = document.getElementById('tab-' + tabId);
    if (activeBtn) {
        activeBtn.classList.remove('opacity-60');
        activeBtn.classList.add('opacity-100', 'text-white', 'border-b-2', 'border-green-500');
    }

    const actionBar = document.getElementById('action-bar');
    if (actionBar) actionBar.classList.toggle('hidden', tabId !== 'tests');

    const grid = document.getElementById('grid');
    const configsContainer = document.getElementById('configs-container');
    const diagContainer = document.getElementById('diag-container');

    if (grid) grid.classList.add('hidden');
    if (configsContainer) configsContainer.classList.add('hidden');
    if (diagContainer) diagContainer.classList.add('hidden');

    if (tabId === 'configs') {
        if (configsContainer) {
            configsContainer.classList.remove('hidden');
            ConfigTab.loadContent(); 
            const configManager = globalMenu.find(m => (m.label || '').toUpperCase().includes('CONFIG'));
            if (configManager && configManager.children) {
                const targetArea = document.getElementById('dynamic-target-setup');
                const netArea = document.getElementById('dynamic-network-setup');
                if (netArea) netArea.innerHTML = ''; 
                if (targetArea) targetArea.innerHTML = renderTree(configManager.children, false, currentTab, 0);
            }
        }
        return; 
    } 

    if (tabId === 'diag') {
        if (diagContainer) {
            diagContainer.classList.remove('hidden');
            if(typeof DiagTab !== 'undefined') DiagTab.init(); 
            
            const diagManager = globalMenu.find(m => (m.label || '').toUpperCase().includes('DIAGNOSTIC'));
            if (diagManager && diagManager.children) {
                const targetArea = document.getElementById('diag-menu-buttons');
                if (targetArea) {
                    targetArea.innerHTML = `
                        <div class="bg-[#0a0a0a] border border-zinc-800/80 rounded-lg p-5 shadow-md flex flex-col w-full h-full">
                            <h3 class="text-white text-[14px] font-bold mb-4 border-b border-zinc-800 pb-3 uppercase tracking-wider">
                                ⚡ Действия
                            </h3>
                            <div class="flex flex-col gap-3">
                                ${renderTree(diagManager.children, false, currentTab, 0)}
                            </div>
                        </div>
                    `;
                    targetArea.className = "flex flex-col h-full";
                }
            }
        }
        return; 
    }

    if (grid) grid.classList.remove('hidden');
    let sectionToRender = [];
    
    if (tabId === 'tests') {
        sectionToRender = globalMenu.filter(m => {
            const lbl = (m.label || '').toUpperCase();
            return !lbl.includes('EXPLORER') && !lbl.includes('PROFILE') && 
                   !lbl.includes('DIAGNOSTIC') && !lbl.includes('CONFIG') && 
                   !lbl.includes('LOG') && !lbl.includes('STATUS');
        });
    } 
    else if (tabId === 'profiles') {
        sectionToRender = globalMenu.filter(m => {
            const lbl = (m.label || '').toUpperCase();
            return lbl.includes('EXPLORER') || lbl.includes('PROFILE');
        });
    }
    else if (tabId === 'logs') {
        sectionToRender = globalMenu.filter(m => {
            const lbl = (m.label || '').toUpperCase();
            return lbl.includes('LOG') || lbl.includes('REPORT') || lbl.includes('STORAGE');
        });
    }

    if (tabId === 'profiles') {
        let profilesData = sectionToRender;
        while (profilesData.length === 1 && profilesData[0].children) {
            profilesData = profilesData[0].children;
        }
        if (grid) grid.innerHTML = renderTree(profilesData, true, currentTab);
    } else {
        if (grid) grid.innerHTML = renderTree(sectionToRender, tabId === 'tests', currentTab);
    }
}

// --- 4. ОБРАБОТЧИКИ СОБЫТИЙ И ЗАПУСК (HANDLERS) ---
async function runAllScenarios() {
    if (!confirm("🚀 Вы уверены, что хотите запустить ВСЕ сценарии текущего конфига в пакетном режиме?")) return;
    try {
        await fetch('/api/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                label: "Batch Run All Scenarios",
                type: "python",
                module: "runners.scenario_runner",
                function: "run_all_batch",
                args: []
            })
        });
        Terminal.show();
        Terminal.printHTML("Starting Batch Execution...");
    } catch (error) {}
}

function executeNode(btnElement) {
    const payload = JSON.parse(btnElement.getAttribute('data-payload'));
    const promptMsg = btnElement.getAttribute('data-prompt');
    if (promptMsg) payload.confirm_msg = promptMsg; 
    processPayload(payload);
}

function executeDropdown(selectId) {
    const payload = JSON.parse(document.getElementById(selectId).value);
    processPayload(payload);
}

// --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ГЕНЕРАЦИИ ПОЛЕЙ ---
function buildFieldHtml(actorIndex, fieldKey, fieldLabel, defaultValue) {
    const inputId = `mod_a${actorIndex}_${fieldKey}`;
    const val = (defaultValue !== undefined && defaultValue !== null) ? defaultValue : '';
    return `
    <div class="flex items-center justify-between mt-2">
        <span class="text-gray-300 text-xs">${fieldLabel}</span>
        <input type="text" id="${inputId}" data-actor="${actorIndex}" data-key="${fieldKey}" value="${val}" class="w-28 bg-black border border-zinc-700 text-green-400 rounded px-2 py-1 text-sm outline-none focus:border-green-500 text-center" placeholder="auto">
    </div>`;
}

// 🚀 НОВАЯ ФУНКЦИЯ РЕНДЕРА МОДАЛКИ
function processPayload(payload) {
    if (payload.preset === 'custom' || payload.label.includes('Custom')) {
        pendingPayload = payload;
        
        // 🔍 УМНЫЙ ПОИСК ЛЕЙБЛА (обходим баг с отсутствующим scen_id)
        const actualScenId = payload.scen_id || (payload.args ? payload.args[0] : null);
        let prettyLabel = payload.label; 
        
        if (actualScenId && globalMenu.length > 0) {
            const findParentLabel = (nodes) => {
                for (const node of nodes) {
                    if (node.children) {
                        // Ищем кнопку, у которой scen_id ИЛИ первый аргумент равен нашему ID
                        if (node.children.some(c => c.scen_id === actualScenId || (c.args && c.args[0] === actualScenId))) {
                            return node.label;
                        }
                        const found = findParentLabel(node.children);
                        if (found) return found;
                    }
                }
                return null;
            };
            const foundLabel = findParentLabel(globalMenu);
            if (foundLabel) {
                // ✂️ МАГИЯ: Отрезаем "SYN1_UDP_STEP: " от названия
                // Если есть двоеточие, берем всё что после него. Если нет - оставляем как есть.
                prettyLabel = foundLabel.includes(': ') ? foundLabel.split(': ').slice(1).join(': ') : foundLabel;
            }
        }

        document.getElementById('modal-title').innerHTML = `⚙️ Настройка параметров:<span class="text-green-500 text-[13px] tracking-normal leading-tight ml-2">${prettyLabel}</span>`;
        
        const container = document.getElementById('dynamic-modal-fields');
        container.innerHTML = ''; 
        
        const reportCb = document.getElementById('mod-report');
        if (reportCb) reportCb.checked = false;

        // 🧠 Вытаскиваем схему, которую нам бережно собрал Питон
        const schema = payload.custom_schema || {};
        const targetActors = schema.actors || [];

        let html = `
        <div class="bg-zinc-900/50 p-3 rounded border border-zinc-800 mb-4 shadow-sm">
            <label class="block text-zinc-400 text-[10px] font-bold uppercase tracking-widest mb-1">Общие параметры</label>
            ${buildFieldHtml('global', 'duration', 'Duration (сек)', schema.duration)}
        `;

        // Добавляем шаги для смарт-степперов
        if (payload.is_series || schema.repeats !== undefined) {
            if (schema.repeats !== undefined && schema.repeats !== "") html += buildFieldHtml('global', 'repeats', 'Repeats (Кол-во шагов)', schema.repeats);
            if (schema.interval !== undefined && schema.interval !== "") html += buildFieldHtml('global', 'interval', 'Interval (Пауза, сек)', schema.interval);
        }
        html += `</div>`;

        // Рендерим акторов
        if (targetActors.length > 0) {
            targetActors.forEach((actor, i) => {
                const isTRex = actor.tool === 'trex';
                const title = isTRex ? '⚡ L3/L4 (TRex)' : '🌐 L7 (JMeter)';
                const color = isTRex ? 'text-blue-400' : 'text-purple-400';
                const profileName = actor.profile || 'default';

                html += `
                <div class="bg-zinc-900/50 p-3 rounded border border-zinc-800 mb-4 shadow-sm">
                    <label class="block text-zinc-400 text-[10px] font-bold uppercase tracking-widest mb-2 flex items-center gap-2">
                        <span class="${color}">${title}</span> <span class="text-zinc-500 normal-case tracking-normal">(${profileName})</span>
                    </label>`;

                if (isTRex) {
                    let multVal = actor.overridemult;
                    // Обработка смарт-степперов ({start: 5, step: 5})
                    if (typeof multVal === 'object' && multVal !== null) {
                        html += buildFieldHtml(i, 'mult_start', 'Multiplier (Start)', multVal.start);
                        html += buildFieldHtml(i, 'mult_step', 'Multiplier (Step)', multVal.step);
                    } else {
                        html += buildFieldHtml(i, 'overridemult', 'Multiplier', multVal);
                    }

                    // Вытаскиваем Tunables
                    if (actor.tunables) {
                        html += `<div class="mt-3 pt-2 border-t border-zinc-800/50">
                                    <span class="text-zinc-500 text-[9px] uppercase tracking-wider block mb-1">Tunables</span>`;
                        for (const [tKey, tVal] of Object.entries(actor.tunables)) {
                            if (typeof tVal === 'number' || (typeof tVal === 'string' && !tVal.includes('.'))) {
                                html += buildFieldHtml(i, `tunable_${tKey}`, tKey, tVal);
                            }
                        }
                        html += `</div>`;
                    }
                } else {
                    html += buildFieldHtml(i, 'threads', 'Threads', actor.threads);
                    html += buildFieldHtml(i, 'override_tput', 'Target RPS', actor.override_tput);
                }
                html += `</div>`;
            });
        } else {
            html += `<div class="text-zinc-500 text-xs italic mt-4 text-center">Настройки акторов не найдены в схеме</div>`;
        }

        container.innerHTML = html;
        document.getElementById('custom-modal').classList.remove('hidden');
        return;
    } 

    if (payload.confirm_msg) {
        if (!confirm(`⚠️ ИНСТРУКЦИЯ ПЕРЕД ЗАПУСКОМ:\n\n${payload.confirm_msg}\nНажмите ОК для продолжения.`)) return; 
        executeFinal(payload, null); 
        return; 
    }

    if (!confirm(`🚀 Выполнить: ${payload.label}\n\nНажмите ОК для продолжения.`)) return;
    executeFinal(payload, null);
}

// 🚀 НОВАЯ ФУНКЦИЯ СБОРКИ (ПАКУЕТ В JSON ДЛЯ ПИТОНА)
function submitCustomModal() {
    if (!pendingPayload || !pendingPayload.custom_schema) {
        closeCustomModal();
        return;
    }
    
    let payloadToRun = JSON.parse(JSON.stringify(pendingPayload)); 
    const wantsReport = document.getElementById('mod-report') ? document.getElementById('mod-report').checked : false;

    let customOverrides = { actors: {} };
    const inputs = document.querySelectorAll('#dynamic-modal-fields input[data-actor]');
    
    inputs.forEach(input => {
        const aIdx = input.getAttribute('data-actor');
        const fKey = input.getAttribute('data-key');
        const val = input.value;
        
        if (!val) return;
        const numVal = isNaN(val) ? val : Number(val);

        if (aIdx === 'global') {
            customOverrides[fKey] = numVal;
            return;
        }

        // Связываем инпут с правильным актором по индексу
        const schemaActor = payloadToRun.custom_schema.actors[aIdx];
        if (!schemaActor) return;
        
        const aKey = schemaActor.profile || schemaActor.tool || 'baseline';
        if (!customOverrides.actors[aKey]) customOverrides.actors[aKey] = {};

        // Раскладываем по полочкам (tunables, mult и т.д.)
        if (fKey.startsWith('tunable_')) {
            const tKey = fKey.replace('tunable_', '');
            if (!customOverrides.actors[aKey].tunables) customOverrides.actors[aKey].tunables = {};
            customOverrides.actors[aKey].tunables[tKey] = numVal;
        } else if (fKey === 'mult_start' || fKey === 'mult_step') {
            if (!customOverrides.actors[aKey].overridemult) customOverrides.actors[aKey].overridemult = {};
            const subKey = fKey.replace('mult_', '');
            customOverrides.actors[aKey].overridemult[subKey] = numVal;
        } else {
            customOverrides.actors[aKey][fKey] = numVal;
        }
    });

    closeCustomModal(); 
    
    let argsArr = [];
    argsArr.push(`--custom-payload '${JSON.stringify(customOverrides)}'`);
    if (wantsReport) argsArr.push("--report");

    executeFinal(payloadToRun, argsArr.join(' '));
}

function closeCustomModal() {
    document.getElementById('custom-modal').classList.add('hidden');
    pendingPayload = null;
}

// ⌨️ ЛОВИМ НАЖАТИЕ ESCAPE ДЛЯ ЗАКРЫТИЯ МОДАЛКИ
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' || event.key === 'Esc') {
        const modal = document.getElementById('custom-modal');
        // Проверяем, существует ли модалка и открыта ли она (нет класса hidden)
        if (modal && !modal.classList.contains('hidden')) {
            closeCustomModal();
        }
    }
});

async function executeFinal(payload, customArgs) {
    Terminal.show();
    
    const isTestScenario = payload.module === 'runners.scenario_runner';
    const isProfileExplorer = payload.module === 'tools.profile_explorer';

    if (isTestScenario || isProfileExplorer) {
        Terminal.setLogType('session');
        Terminal.printHTML(`\n<span class="text-yellow-500 font-bold">--- 🚀 [TEST INITIATED] Переключение на канал боевых логов... ---</span>\n\n`);

        let fetchOptions = { method: 'POST' };
        if (customArgs) {
            fetchOptions.headers = { 'Content-Type': 'application/json' };
            fetchOptions.body = JSON.stringify({ custom_args: customArgs });
        }

        let scId = "unknown";
        let preset = "default";

        if (isTestScenario && payload.args && payload.args.length >= 1) {
            scId = payload.args[0]; 
            preset = payload.args[1] || "default"; 
        } else if (isProfileExplorer && payload.args && payload.args.length >= 2) {
            scId = "adhoc_" + payload.args[0];
            preset = "default";
        }
        fetch(`/api/run/${scId}/${preset}`, fetchOptions);
    } else {
        Terminal.setLogType('web');
        Terminal.printHTML(`\n<span class="text-purple-400">[SYSTEM] Выполняю: ${payload.label}...</span>`);

        try {
            if (customArgs) payload.custom_args = customArgs; 
            const response = await fetch('/api/execute', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            const statusColor = result.status === 'success' ? 'text-green-400' : 'text-red-500';
            
            Terminal.printHTML(`\n<span class="${statusColor}">[RESULT] ${result.output}</span>\n`);
        } catch(e) {
            Terminal.printHTML(`\n<span class="text-red-500">[FATAL] Ошибка: ${e.message}</span>\n`);
        }
    }
}

async function activateKillSwitch() {
    if(confirm("🛑 ВНИМАНИЕ!\nЭто немедленно убьет все запущенные тесты.\nПРОДОЛЖИТЬ?")) {
        Terminal.show();
        try {
            await fetch('/api/kill', { method: 'POST' });
            updateStatus();
        } catch(e) {}
    }
}

// --- 5. ИНИЦИАЛИЗАЦИЯ И ТАЙМЕРЫ (BOOTSTRAP) ---
window.addEventListener('DOMContentLoaded', () => {
    const configsEl = document.getElementById('configs-container');
    if(configsEl) {
        configsEl.innerHTML = buildViewerTemplate({
            leftColumnId: 'dynamic-target-setup',
            leftColumnExtraHtml: '<div id="dynamic-network-setup"></div>',
            iconColor: 'text-yellow-500', icon: '📄', title: 'Просмотр файлов конфигураций',
            pathId: 'configFilePath', defaultPath: '/opt/pmi/config/test_program.yaml',
            selectId: 'configViewerSelect', onSelectChange: 'loadConfigContent()',
            textColor: 'text-green-400', accentColor: 'green', contentId: 'configContentBox'
        });
    }

    const diagEl = document.getElementById('diag-container');
    if(diagEl) {
        diagEl.innerHTML = buildViewerTemplate({
            leftColumnId: 'diag-menu-buttons', 
            iconColor: 'text-zinc-300', icon: '📄', title: 'Просмотр файлов диагностики',
            pathId: 'diagFilePath', defaultPath: '/opt/pmi/logs/latest/...',
            selectId: 'diagViewerSelect', onSelectChange: 'loadDiagContent()',
            textColor: 'text-zinc-300', accentColor: 'blue', contentId: 'diagContentBox',
            scrollContainerId: 'diagScrollContainer',
            toolbarHtml: `
                <div class="relative hidden md:block w-[150px] xl:w-[180px]">
                    <span class="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-600 text-[10px]">🔍</span>
                    <input type="text" id="diagSearchInput" placeholder="Фильтр лога..." oninput="filterDiagLog(this.value)" class="w-full bg-[#151515] border border-zinc-700 text-gray-300 rounded pl-7 pr-2 py-1.5 text-[11px] outline-none focus:border-zinc-500 font-mono">
                </div>
                <button onclick="initDiagViewer()" class="text-zinc-400 hover:text-white bg-[#151515] border border-zinc-700 hover:border-zinc-500 px-2.5 py-1.5 rounded transition flex items-center justify-center" title="Обновить список">🔄</button>
                <button onclick="downloadCurrentDiag()" class="text-blue-400 hover:text-white bg-blue-900/20 border border-blue-800 hover:bg-blue-600 px-2.5 py-1.5 rounded transition flex items-center justify-center" title="Скачать лог">⬇️</button>
            `
        });
    }

    loadConfigSelector(); 
    loadMainMenu();
    updateStatus(); 
    
    ConfigTab.init(); 
    Terminal.init();  
    
    // 🟢 Внедряем модалку паузы из ui_templates.js в DOM
    if (typeof buildPauseModalTemplate === 'function') {
        document.body.insertAdjacentHTML('beforeend', buildPauseModalTemplate());
    }

    setInterval(updateStatus, 2000);
    setInterval(checkPauseState, 2000);
});

// --- 6. УПРАВЛЕНИЕ ПАУЗАМИ И ОВЕРЛЕЯМИ (ORCHESTRATOR SYNC) ---

async function checkPauseState() {
    if (isHandlingPause) return;
    try {
        const r = await fetch('/api/session/state');
        const d = await r.json();
        
        if (d.status === 'pause') {
            isHandlingPause = true;
            
            // Вставляем текст промпта
            const promptEl = document.getElementById('pause-prompt-text');
            if (promptEl) promptEl.innerText = d.prompt;
            
            // Управляем видимостью галочки IPS
            const ipsContainer = document.getElementById('pause-ips-container');
            const ipsCheckbox = document.getElementById('pause-ips-checkbox');
            
            if (ipsContainer && ipsCheckbox) {
                if (d.show_ips_toggle) {
                    ipsContainer.classList.remove('hidden');
                    ipsCheckbox.checked = false; // Сбрасываем стейт с прошлого раза
                } else {
                    ipsContainer.classList.add('hidden');
                    ipsCheckbox.checked = false;
                }
            }
            
            // Показываем модалку
            const modalEl = document.getElementById('pause-modal');
            if (modalEl) modalEl.classList.remove('hidden');
        }
    } catch(e) {}
}

async function resumePausedSession() {
    const ipsCheckbox = document.getElementById('pause-ips-checkbox');
    const wantsIps = ipsCheckbox ? ipsCheckbox.checked : false;

    // Прячем модалку
    const modalEl = document.getElementById('pause-modal');
    if (modalEl) modalEl.classList.add('hidden');

    try {
        await fetch('/api/session/resume', { 
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                options: {
                    inject_ips: wantsIps // 🟢 Передаем выбор инженера в бэкенд
                }
            })
        });
    } catch(e) {
        console.error("Ошибка при отправке команды продолжения:", e);
    } finally {
        isHandlingPause = false; // Отпускаем блокировку для поллинга
    }
}