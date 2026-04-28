// ==========================================
// ФАЙЛ: terminal.js
// ОТВЕТСТВЕННОСТЬ: Логика консоли, поллинг, автоскролл, подсветка
// ==========================================

const Terminal = {
    logInterval: null,
    currentLogType: 'web',
    isUserScrolling: false,

    init: function() {
        const termContainer = document.getElementById('terminal-container');
        if (!termContainer) return;
        
        // Отслеживание скролла пользователя (чтобы не сбивать чтение)
        termContainer.addEventListener('scroll', () => {
            const isAtBottom = termContainer.scrollHeight - termContainer.scrollTop <= termContainer.clientHeight + 10;
            this.isUserScrolling = !isAtBottom;
        });
        
        this.startPolling();
    },

    startPolling: function() {
        if (this.logInterval) clearInterval(this.logInterval);
        
        this.logInterval = setInterval(async () => {
            try {
                // 1. Проверяем статус бэкенда
                const statusRes = await fetch('/api/status');
                const statusData = await statusRes.json();
                
                const newLogType = statusData.is_running ? 'session' : 'web';
                const termContent = document.getElementById('term-content');
                
                // 2. Если статус изменился, переключаем канал автоматически
                if (this.currentLogType !== newLogType && termContent) {
                    this.currentLogType = newLogType;
                    const prefix = this.currentLogType === 'session' ? '🚀 [TEST STARTED] Свистать всех наверх!' : '💤 [SYSTEM IDLE] Ждем команд...';
                    this.printHTML(`\n<span class="text-yellow-500 font-bold">--- ${prefix} ---</span>\n\n`);
                }

                // 3. Запрашиваем логи
                const logRes = await fetch(`/api/logs?type=${this.currentLogType}`);
                const logData = await logRes.json();
                
                if (logData.logs && termContent) {
                    let formattedLogs = logData.logs;
                    
                    // Обработка очистки экрана
                    if (formattedLogs.includes('[PMI_CLEAR_SCREEN]')) {
                        const parts = formattedLogs.split('[PMI_CLEAR_SCREEN]');
                        formattedLogs = parts[parts.length - 1].trimStart();
                    }

                    // Подсветка для системных логов
                    if (this.currentLogType === 'web') {
                        formattedLogs = formattedLogs
                            .replace(/\[WEB\]/g, '<span class="text-blue-400">[WEB]</span>')
                            .replace(/\[INFO\]/g, '<span class="text-gray-400">[INFO]</span>')
                            .replace(/\[ERROR\]/g, '<span class="text-red-500 font-bold">[ERROR]</span>');
                    }
                    
                    // Защита от сброса выделения мышкой
                    const selection = window.getSelection();
                    const hasSelection = selection.toString().length > 0 && termContent.contains(selection.anchorNode);

                    if (!hasSelection && termContent.innerHTML !== formattedLogs) {
                        termContent.innerHTML = formattedLogs;
                        this.scrollToBottom();
                    }
                }
            } catch (e) {
                // Игнорируем ошибки сети при поллинге
            }
        }, 1500); 
    },

    clear: async function() {
        const termContent = document.getElementById('term-content');
        try {
            const res = await fetch('/api/logs/clear', { method: 'POST' });
            const data = await res.json();
            if (termContent) termContent.innerHTML = `\n<span class="text-green-400">[SYSTEM] ${data.output || 'Logs cleared'}</span>\n`;
        } catch(e) {}
    },

    // --- API ДЛЯ ВЗАИМОДЕЙСТВИЯ ИЗ APP.JS ---

    show: function() {
        const termContainer = document.getElementById('terminal-container');
        if (termContainer) termContainer.classList.remove('hidden');
    },

    setLogType: function(type) {
        this.currentLogType = type;
    },

    printHTML: function(htmlStr) {
        const termContent = document.getElementById('term-content');
        if (termContent) {
            termContent.innerHTML += htmlStr;
            this.scrollToBottom();
        }
    },

    scrollToBottom: function() {
        if (!this.isUserScrolling) {
            const termContainer = document.getElementById('terminal-container');
            if (termContainer) termContainer.scrollTop = termContainer.scrollHeight;
        }
    }
};

// Прокидываем функцию глобально, чтобы не ломать старый HTML с onclick="clearTerminal()"
window.clearTerminal = () => Terminal.clear();