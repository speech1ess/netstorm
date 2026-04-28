// ==========================================
// ФАЙЛ: ui_templates.js
// ОТВЕТСТВЕННОСТЬ: Только генерация HTML-строк
// ==========================================

// 🟢 1. ГЕНЕРАТОР ШАБЛОНА ВЬЮВЕРА (Для Конфигов и Диагностики)
function buildViewerTemplate(options) {
    return `
        <div class="flex flex-col lg:flex-row gap-6">
            
            <div class="lg:w-1/3 flex flex-col gap-6" id="${options.leftColumnId}">
                ${options.leftColumnExtraHtml || ''}
            </div>                
            
            <div class="lg:w-2/3 flex flex-col h-full">
                <div class="bg-[#0a0a0a] border border-zinc-800/80 rounded-lg p-5 shadow-md flex-grow flex flex-col min-h-[600px]">
                    
                    <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-4 border-b border-zinc-800 pb-3 gap-3">
                        <div>
                            <h3 class="text-white text-[14px] font-bold flex items-center gap-2 m-0 tracking-wider">
                                <span class="${options.iconColor || 'text-yellow-500'}">${options.icon || '📄'}</span> ${options.title}
                            </h3>
                            <div id="${options.pathId}" class="text-zinc-500 text-[10px] mt-1 font-mono tracking-widest">${options.defaultPath}</div>
                        </div>
                        
                        <div class="flex items-center gap-2 w-full sm:w-auto justify-end">
                            ${options.toolbarHtml || ''}
                            
                            <div class="relative w-full sm:w-[220px]">
                                <select id="${options.selectId}" class="w-full bg-[#151515] border border-zinc-700 ${options.textColor} font-bold rounded pl-3 pr-8 py-1.5 text-[11px] uppercase tracking-wider outline-none focus:border-${options.accentColor || 'green'}-500 appearance-none cursor-pointer truncate" onchange="${options.onSelectChange}">
                                    <option value="">Загрузка списка...</option>
                                </select>
                                <div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 ${options.textColor}/50 text-[10px]">▼</div>
                            </div>
                        </div>
                    </div>
                    
                    <div ${options.scrollContainerId ? `id="${options.scrollContainerId}"` : ''} class="flex-grow bg-black border border-zinc-700 rounded p-4 overflow-y-auto shadow-[inset_0_0_20px_rgba(0,0,0,1)] relative">
                        <pre class="m-0 whitespace-pre-wrap absolute inset-0 p-4"><code id="${options.contentId}" class="font-mono text-[11px] ${options.textColor} leading-relaxed block h-full"># Ожидание выбора файла...</code></pre>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// 🟢 2. ГЕНЕРАТОР ДЕРЕВА МЕНЮ (Карточки, кнопки, папки)
function renderTree(nodes, isTestsTab, currentTabStr, depth = 0) {
    if (!nodes) return '';
    return nodes.map(node => {
        // Пропускаем системные узлы
        if (node.type === 'exit' || (node.label && (node.label.includes('--- SOURCE:') || node.label.includes('RUN ALL SCENARIOS')))) {
            return '';
        }

        if (node.type === 'folder') {
            const isDropdownCard = node.children && node.children.length > 0 && node.children.every(c => c.type === 'python' || c.type === 'command');

            // 🛠️ РЕНДЕР КАРТОЧКИ С ВЫПАДАЮЩИМ СПИСКОМ
            if (isDropdownCard) {
                // 1. Убираем дублирование ID из заголовка. 
                // Предполагаем, что node.label выглядит как "ID: Название [ID] (Параметры)"
                // Режем строку по первой двоеточию и берем всё, что справа.
                let cleanTitle = node.label;
                const colonIndex = cleanTitle.indexOf(':');
                if (colonIndex !== -1) {
                    cleanTitle = cleanTitle.substring(colonIndex + 1).trim();
                }
                
                // Убираем старый мусор, если он есть
                cleanTitle = cleanTitle.replace(' + Baseline', '').replace(' + Base', '').replace('📄 ', '');

                let selectId = 'sel-' + Math.random().toString(36).substr(2, 9);
                let btnColor = isTestsTab ? "bg-green-900/20 text-green-400 border-green-800 hover:bg-green-500 hover:text-black" : "bg-blue-900/20 text-blue-400 border-blue-800 hover:bg-blue-500 hover:text-white";
                let btnText = isTestsTab ? "ЗАПУСК" : "ВЫПОЛНИТЬ";
                
                let emoji = '⚙️';
                if (isTestsTab) emoji = '🎯';
                else if (node.label.includes('Diagnostic')) emoji = '🛠️';
                else if (node.label.includes('Restart')) emoji = '🔄';
                else if (node.label.includes('Storage') || node.label.includes('Clean')) emoji = '🧹';

                if (currentTabStr === 'profiles') emoji = '📄';

                const hasSeries = node.children.some(p => p.is_series);
                const badgeHtml = hasSeries && isTestsTab 
                    ? `<span class="ml-2 text-[9px] bg-amber-500 text-black px-2 py-0.5 rounded font-black uppercase tracking-wider whitespace-nowrap flex-shrink-0 shadow-sm">Серия</span>`
                    : '';

                // Пытаемся достать prompt из первого ребенка, если он там есть
                const tooltipText = (node.children[0] && node.children[0].confirm_msg) 
                    ? node.children[0].confirm_msg.replace(/"/g, '&quot;') 
                    : '';

                return `
                <div class="bg-zinc-900/60 border border-zinc-800 rounded-lg p-4 flex flex-col justify-between hover:border-zinc-600 transition shadow-lg group">
                    <div class="text-[12px] font-bold text-gray-200 mb-4 pb-2 border-b border-zinc-800 group-hover:text-white transition leading-relaxed flex items-start justify-between break-words gap-2" title="${tooltipText}">
                        <div class="flex items-center">
                            <span class="mr-2 opacity-70 cursor-help">${emoji}</span> ${cleanTitle}
                        </div>
                        ${badgeHtml}
                    </div>
                    <div class="flex items-center gap-2">
                        <div class="relative flex-grow">
                            <select id="${selectId}" class="w-full bg-[#050505] border border-zinc-700 text-gray-300 text-[11px] rounded px-3 py-2 outline-none focus:border-zinc-500 cursor-pointer">
                                ${node.children.map(p => {
                                    // 2. Берем ИМЯ ПРЕСЕТА (p.preset_name) или оставляем чистый label без системного мусора
                                    let optText = p.preset_name || p.label;
                                    
                                    // Если бэкенд не отдает preset_name, чистим label
                                    if(!p.preset_name) {
                                       optText = optText.replace('Default (As defined)', 'Default')
                                            .replace('⚙️ Custom Load (Interactive)', 'Custom')
                                            .replace(/ Load$/, '')
                                            .replace('► ', '')
                                            .replace('👁️ ', '')
                                            .replace('🚀 ', '')
                                            .replace('⚙️ ', '')
                                            .replace(' (cat)', '');
                                    }

                                    const safePayload = JSON.stringify(p).replace(/'/g, "&#39;").replace(/"/g, "&quot;");
                                    
                                    let isSelected = '';
                                    const upperText = optText.toUpperCase();
                                    if (upperText.includes('STATUS') || upperText.includes('CHEATSHEET') || upperText.includes('PMI-WEB') || upperText.includes('DEFAULT')) {
                                        isSelected = 'selected';
                                    }

                                    return `<option value="${safePayload}" ${isSelected}>${optText}</option>`;
                                }).join('')}
                            </select>
                            <div class="pointer-events-none absolute inset-y-0 right-0 flex items-center px-2 text-zinc-500">▼</div>
                        </div>
                        <button onclick="executeDropdown('${selectId}')" class="${btnColor} border px-4 py-2 rounded text-[11px] transition font-bold tracking-wider whitespace-nowrap">
                            ${btnText}
                        </button>
                    </div>
                </div>`;
            } else {
                // 🛠️ РЕНДЕР ПАПОК И ГРУПП
                if (node.label.includes('Test & Scenarios') || node.label.includes('PMI Scenarios')) {
                    return `<div class="mb-2">${renderTree(node.children, isTestsTab, currentTabStr, depth)}</div>`;
                }
                
                const isRoot = depth === 0;

                if (isRoot && !isTestsTab) {
                    const buttons = node.children.filter(c => c.type === 'python' || c.type === 'command');
                    const subfolders = node.children.filter(c => c.type === 'folder');
                    
                    const hasButtons = buttons.length > 0;
                    const hasFolders = subfolders.length > 0;
                    const gridClass = (hasButtons && hasFolders) ? "grid grid-cols-1 lg:grid-cols-2 gap-8 items-start" : "grid grid-cols-1 gap-6 items-start shadow-md gap-4";

                    return `
                    <div class="mb-8 bg-[#0a0a0a] p-6 rounded-lg border border-zinc-800/50 shadow-md">
                        <h2 class="text-white text-[16px] underline underline-offset-4 decoration-green-500 decoration-2 font-bold uppercase tracking-widest border-b border-zinc-800 mb-5 pb-2 flex items-center">
                            <span class="bg-zinc-800 w-2 h-2 rounded-full mr-3"></span> ${node.label.replace('📁 ', '')}
                        </h2>
                        <div class="${gridClass}">
                            ${hasButtons ? `
                            <div class="flex flex-col gap-3">
                                <h3 class="text-zinc-600 text-[9px] uppercase tracking-widest mb-2 ml-1">Действия / Проверки</h3>
                                ${renderTree(buttons, isTestsTab, currentTabStr, depth + 1)}
                            </div>` : ''}
                            
                            ${hasFolders ? `
                            <div class="flex flex-col gap-4">
                                ${hasButtons ? `<h3 class="text-zinc-600 text-[9px] uppercase tracking-widest mb-1 ml-1">Сервисы / Управление</h3>` : ''}
                                <div class="${hasButtons ? 'flex flex-col gap-4' : 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5'}">
                                    ${renderTree(subfolders, isTestsTab, currentTabStr, depth + 1)}
                                </div>
                            </div>` : ''}
                        </div>
                    </div>`;
                } else {
                    const containerClass = (isTestsTab && isRoot) ? "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 items-start" : "flex flex-col gap-2 mt-3";
                    const wrapperClass = isRoot ? "mb-8 bg-[#0a0a0a] p-6 rounded-lg border border-zinc-800/50" : "ml-2 mt-4 mb-2 border-l border-zinc-800 pl-4";
                    const titleClass = isRoot ? "text-white text-[16px] underline underline-offset-4 decoration-green-500 decoration-2 font-bold uppercase tracking-widest border-b border-zinc-800 mb-5 pb-2 flex items-center" : "text-zinc-500 text-[10px] font-bold uppercase tracking-widest mb-3 flex items-center";

                    return `
                    <div class="${wrapperClass}">
                        <h2 class="${titleClass}">
                            <span class="${isRoot ? 'bg-zinc-800 w-2 h-2 rounded-full mr-3' : 'mr-2'}">${isRoot ? '' : '📁'}</span> ${node.label.replace('📁 ', '')}
                        </h2>
                        <div class="${containerClass}">
                            ${renderTree(node.children, isTestsTab, currentTabStr, depth + 1)}
                        </div>
                    </div>`;
                }
            }
        } else if (node.type === 'python' || node.type === 'command') {
            // 🛠️ РЕНДЕР ПРОСТЫХ КНОПОК
            const safePayload = JSON.stringify(node).replace(/'/g, "&#39;").replace(/"/g, "&quot;");
            const safePrompt = node.confirm_msg ? node.confirm_msg.replace(/'/g, "&#39;").replace(/"/g, "&quot;") : '';
            
            let btnColor = "border-zinc-700 bg-zinc-800 hover:border-green-500 hover:text-green-400";
            if(node.label.includes('Restart') || node.label.includes('Regenerate')) btnColor = "border-blue-900/50 bg-blue-900/10 hover:bg-blue-600 hover:text-white text-blue-400";
            if(node.label.includes('Delete') || node.label.includes('Force') || node.label.includes('Kill')) btnColor = "border-red-900/50 bg-red-900/10 hover:bg-red-600 hover:text-white text-red-400";
            
            const isSeries = node.is_series;
            const badgeHtml = isSeries && isTestsTab 
                ? `<span class="ml-auto text-[9px] bg-amber-500 text-black px-2 py-0.5 rounded font-black uppercase tracking-wider whitespace-nowrap flex-shrink-0 shadow-sm">Серия</span>`
                : '';

            return `<button onclick="executeNode(this)" data-payload="${safePayload}" data-prompt="${safePrompt}"
                class="w-full border px-4 py-3 rounded text-[11px] text-gray-300 transition text-left flex items-center shadow-sm ${btnColor}">
                <span class="mr-3 opacity-50">⚡</span> 
                <span>${node.label}</span>
                ${badgeHtml}
            </button>`;
        } else if (node.type === 'error' || (node.label && node.label.includes('Error'))) {
            return `
            <div class="col-span-full bg-red-900/30 border border-red-800 rounded p-6 text-center shadow-lg mt-4">
                <span class="text-4xl mb-4 block">🚨</span>
                <h3 class="text-red-400 font-bold text-lg mb-2">${node.label}</h3>
                <p class="text-zinc-400 text-xs">Не удалось загрузить конфигурацию. Проверьте синтаксис YAML или логи (journalctl -u pmi-web).</p>
            </div>`;
        }   
    }).join('');
}

// 🟢 3. ГЕНЕРАТОР МОДАЛКИ ПАУЗЫ С ОВЕРЛЕЕМ IPS
function buildPauseModalTemplate() {
    return `
    <div id="pause-modal" class="hidden fixed inset-0 z-[100] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="bg-[#0a0a0a] border border-zinc-800 rounded-lg shadow-[0_0_30px_rgba(0,0,0,0.8)] w-full max-w-lg flex flex-col overflow-hidden transform transition-all">
            
            <div class="px-5 py-4 border-b border-zinc-800/80 bg-[#111] flex justify-between items-center">
                <h2 class="text-white text-[14px] font-bold uppercase tracking-wider flex items-center gap-2">
                    <span class="text-yellow-500">⏳</span> Ожидание действий
                </h2>
            </div>
            
            <div class="p-6 flex flex-col gap-5 bg-black/50">
                <div id="pause-prompt-text" class="text-zinc-300 text-[13px] leading-relaxed whitespace-pre-wrap p-4 bg-zinc-900/50 border border-zinc-800 rounded shadow-inner font-mono">
                    </div>

                <div id="pause-ips-container" class="hidden bg-red-900/10 p-4 rounded border border-red-900/30 shadow-sm transition hover:bg-red-900/20 hover:border-red-900/50">
                    <label class="flex items-start gap-3 cursor-pointer group">
                        <input type="checkbox" id="pause-ips-checkbox" class="mt-1 w-4 h-4 rounded bg-black border-zinc-700 text-red-500 focus:ring-red-500/20 cursor-pointer">
                        <div class="flex flex-col">
                            <span class="text-red-400 text-[13px] font-bold uppercase tracking-wider flex items-center gap-2 group-hover:text-red-300 transition">
                                🔥 Активировать Malware Overlay (IPS)
                            </span>
                            <span class="text-zinc-500 text-[11px] mt-1 leading-tight">
                                Добавляет эталонные эксплойты (+20 CPS) поверх легитимного трафика.
                            </span>
                        </div>
                    </label>
                </div>
            </div>
            
            <div class="px-5 py-4 border-t border-zinc-800/80 bg-[#111] flex justify-end gap-3">
                <button onclick="resumePausedSession()" class="bg-green-900/20 text-green-400 border border-green-800 hover:bg-green-500 hover:text-black px-6 py-2 rounded text-[12px] font-bold uppercase tracking-wider transition shadow-lg">
                    Продолжить
                </button>
            </div>
            
        </div>
    </div>
    `;
}