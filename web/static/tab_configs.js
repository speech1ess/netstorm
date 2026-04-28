// ==========================================
// ФАЙЛ: tab_configs.js
// ОТВЕТСТВЕННОСТЬ: Вкладка "Конфигурация"
// ==========================================

const ConfigTab = {
    dynamicConfigPaths: {},

    init: async function() {
        const select = document.getElementById('configViewerSelect');
        if (!select) return;
        
        try {
            const r = await fetch('/api/configs/list');
            const data = await r.json();
            select.innerHTML = ''; 

            if (data.programs && data.programs.length > 0) {
                const groupPrograms = document.createElement('optgroup');
                groupPrograms.label = "🎯 Программы испытаний (ПМИ)";
                data.programs.forEach(cfg => {
                    this.dynamicConfigPaths[cfg.id] = cfg.path;
                    groupPrograms.innerHTML += `<option value="${cfg.id}">${cfg.id}</option>`;
                });
                select.appendChild(groupPrograms);
            }
            if (data.system && data.system.length > 0) {
                const groupSystem = document.createElement('optgroup');
                groupSystem.label = "⚙️ Конфигурация стенда";
                data.system.forEach(cfg => {
                    this.dynamicConfigPaths[cfg.id] = cfg.path;
                    groupSystem.innerHTML += `<option value="${cfg.id}">${cfg.id}</option>`;
                });
                select.appendChild(groupSystem);
            }
            this.syncWithMain();
        } catch (e) {
            console.error("ConfigTab Init Error:", e);
        }
    },

    syncWithMain: function() {
        const mainSelect = document.getElementById('pmi-select');
        const viewerSelect = document.getElementById('configViewerSelect');
        
        if (mainSelect && viewerSelect && this.dynamicConfigPaths[mainSelect.value]) {
            viewerSelect.value = mainSelect.value;
            this.loadContent(); 
        }
    },

    loadContent: async function() {
        const filename = document.getElementById('configViewerSelect').value;
        const codeBox = document.getElementById('configContentBox');
        const pathLabel = document.getElementById('configFilePath');
        
        if (!codeBox) return;
        
        pathLabel.textContent = this.dynamicConfigPaths[filename] || filename;
        codeBox.textContent = '# Чтение ' + filename + ' ...';
        
        try {
            const r = await fetch(`/api/configs/content?filename=${filename}`);
            const data = await r.json();
            codeBox.textContent = data.content || '# Пустой ответ сервера';
        } catch(e) {
            codeBox.textContent = '# Ошибка загрузки файла:\n' + e;
        }
    }
};

// Прокидываем в глобальную область видимости для HTML onclick
window.loadConfigContent = () => ConfigTab.loadContent();