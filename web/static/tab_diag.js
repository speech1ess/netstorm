// ==========================================
// ФАЙЛ: tab_diag.js
// ОТВЕТСТВЕННОСТЬ: Вкладка "Диагностика"
// ==========================================

const DiagTab = {
    currentRawDiagLog: "",

    init: async function() {
        const select = document.getElementById('diagViewerSelect');
        if (!select) return;
        
        try {
            const r = await fetch('/api/diag/files');
            const data = await r.json();
            select.innerHTML = ''; 
            
            if (data.files && data.files.length > 0) {
                let hasLatest = false;
                data.files.forEach(file => {
                    const option = document.createElement('option');
                    option.value = file;
                    option.textContent = file;
                    if (file === 'latest.log' || file === 'pmi_session.log') {
                        option.selected = true;
                        hasLatest = true;
                    }
                    select.appendChild(option);
                });
                
                if (!hasLatest && select.options.length > 0) {
                    select.selectedIndex = 0;
                }
                this.loadContent(); 
            } else {
                select.innerHTML = '<option value="">Логи не найдены</option>';
                document.getElementById('diagContentBox').textContent = '# Папка пуста.';
            }
        } catch (e) {
            console.error("DiagTab Init Error:", e);
        }
    },

    loadContent: async function() {
        const select = document.getElementById('diagViewerSelect');
        const filename = select ? select.value : null;
        const codeBox = document.getElementById('diagContentBox');
        const pathLabel = document.getElementById('diagFilePath');
        const statusLabel = document.getElementById('diagStatus');
        
        if (!filename || !codeBox) return;
        
        pathLabel.textContent = `logs/latest/${filename}`;
        codeBox.textContent = '# Загрузка лога ' + filename + ' ...';
        if(statusLabel) {
            statusLabel.classList.remove('hidden');
            statusLabel.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-yellow-500 animate-pulse"></span> Чтение';
        }
        
        const searchInput = document.getElementById('diagSearchInput');
        if (searchInput) searchInput.value = '';

        try {
            const r = await fetch(`/api/diag/read/${filename}`);
            const data = await r.json();
            
            if (data.content) {
                this.currentRawDiagLog = data.content;
                this.renderColoredLog(this.currentRawDiagLog);
                if(statusLabel) statusLabel.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-green-500"></span> Готов';
            } else {
                this.currentRawDiagLog = "";
                codeBox.textContent = '# Файл пуст';
                if(statusLabel) statusLabel.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-red-500"></span> Пусто';
            }
            
            const container = document.getElementById('diagScrollContainer');
            if (container) setTimeout(() => { container.scrollTop = container.scrollHeight; }, 50);
        } catch(e) {
            this.currentRawDiagLog = "";
            codeBox.textContent = '# Ошибка загрузки лога:\n' + e;
            if(statusLabel) statusLabel.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-red-500"></span> Ошибка';
        }
    },

    renderColoredLog: function(text) {
        const codeBox = document.getElementById('diagContentBox');
        if (!codeBox) return;
        if (!text) { codeBox.innerHTML = ''; return; }

        let safeText = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

        let coloredText = safeText
            .replace(/^.*(\[ERROR\]|Traceback|Exception|Error:|Failed).*$/gm, '<span class="text-red-500 font-bold bg-red-900/20 block">$&</span>')
            .replace(/^.*(\[WARN\]|\[WARNING\]).*$/gm, '<span class="text-yellow-400 block">$&</span>')
            .replace(/(\[INFO\])/g, '<span class="text-blue-400">$1</span>')
            .replace(/(\[TREX\]|\[JMETER\]|\[DBG\])/g, '<span class="text-purple-400 font-bold">$1</span>')
            .replace(/===.*?===/g, '<span class="text-yellow-500 font-bold">$&</span>');

        codeBox.innerHTML = coloredText;
    },

    filterLog: function(query) {
        if (!this.currentRawDiagLog) return;
        
        if (!query || query.trim() === '') {
            this.renderColoredLog(this.currentRawDiagLog);
            return;
        }
        
        const lowerQuery = query.toLowerCase();
        const filteredLines = this.currentRawDiagLog.split('\n').filter(line => line.toLowerCase().includes(lowerQuery));
        
        if (filteredLines.length === 0) {
            document.getElementById('diagContentBox').innerHTML = `<span class="text-zinc-500"># По запросу "${query}" ничего не найдено...</span>`;
        } else {
            this.renderColoredLog(filteredLines.join('\n'));
        }
    },

    download: function() {
        const select = document.getElementById('diagViewerSelect');
        if (select && select.value) {
            const link = document.createElement('a');
            link.href = `/api/diag/download/${select.value}`;
            link.download = select.value;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    }
};

// Прокидываем в глобальную область видимости для HTML onclick
window.initDiagViewer = () => DiagTab.init();
window.loadDiagContent = () => DiagTab.loadContent();
window.filterDiagLog = (q) => DiagTab.filterLog(q);
window.downloadCurrentDiag = () => DiagTab.download();