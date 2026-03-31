const { createApp } = Vue

createApp({
    data() {
        return {
            email: '',
            password: '',
            user: null,
            apiBase: (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
                ? 'http://localhost:5000/api'
                : 'https://teletap-backend.onrender.com/api', // URL prospectiva en Render

            showCryptoModal: false,
            selectedPlan: '',
            selectedPlanName: '',
            selectedAmount: 0,
            pollingInterval: null,
            statusMsg: { text: '', type: '' }
        }
    },

    mounted() {
        this.initIcons();
        // Forzar actualización de la UI cada segundo para los relojes de uptime y descanso
        setInterval(() => {
            this.$forceUpdate();
        }, 1000);
    },
    computed: {
        qrImageUrl() {
            // Genera un QR que contiene la dirección TRC20 y el monto sugerido
            // Para Tron se suele usar el formato: tron:ADDRESS?amount=VALUE
            const uri = `tron:${this.walletAddr}?amount=${this.selectedAmount}`;
            return `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(uri)}`;
        }
    },
    methods: {
        async login() {
            try {
                const res = await fetch(`${this.apiBase}/login`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: this.email, password: this.password })
                });
                const data = await res.json();
                if (data.success) {
                    this.user = data.user;
                    this.initIcons();
                    this.startPolling(); // Iniciar actualización constante
                } else {
                    console.error("Login failed:", data.message);
                }
            } catch (e) {
                console.error("Error connecting to server. Ensure backend is running.", e);
            }
        },

        async updateToken(type) {
            const token = type === 'chainer' ? this.user.token_chainer : this.user.token_roller;
            try {
                const res = await fetch(`${this.apiBase}/update_token`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: this.user.email, bot_type: type, token })
                });
                const data = await res.json();
                if (data.success) {
                    console.log("Token updated successfully.");
                } else {
                    console.error("Failed to update token:", data.message);
                }
            } catch (e) {
                console.error("Error saving token:", e);
            }
        },

        formatLargeNumber(num) {
            if (num === null || num === undefined || isNaN(num) || num === '-') return num;
            const n = parseFloat(num);
            if (n >= 1e12) return (n / 1e12).toFixed(3).replace(/\.?0+$/, '') + 't';
            if (n >= 1e9) return (n / 1e9).toFixed(3).replace(/\.?0+$/, '') + 'b';
            if (n >= 1e6) return (n / 1e6).toFixed(3).replace(/\.?0+$/, '') + 'm';
            if (n >= 1e3) return (n / 1e3).toFixed(3).replace(/\.?0+$/, '') + 'k';
            return n.toString();
        },
        formatTime(seconds) {
            const total = Math.max(0, Math.floor(seconds || 0));
            const hrs = Math.floor(total / 3600);
            const mins = Math.floor((total % 3600) / 60);
            const secs = total % 60;
            return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        },
        getRechargeStatus(bot) {
            const recharges = bot === 'chainer' ? this.user.chainer_recharges : this.user.roller_recharges;
            const rechargeAt = bot === 'chainer' ? this.user.chainer_recharge_at : this.user.roller_recharge_at;
            const token = bot === 'chainer' ? this.user.token_chainer : this.user.token_roller;

            if (!token) return '-';

            const now = Date.now();
            // Priorizar el tiempo restante si existe una fecha de próxima recarga en el futuro
            if (rechargeAt && (rechargeAt * 1000) > now) {
                const diff = Math.ceil((rechargeAt * 1000 - now) / 1000);
                return this.formatTime(diff);
            }

            if (recharges > 0) return 'Recarga Lista!';
            return 'Agotado';
        },
        isRechargeWarning(bot) {
            const recharges = bot === 'chainer' ? this.user.chainer_recharges : this.user.roller_recharges;
            const rechargeAt = bot === 'chainer' ? this.user.chainer_recharge_at : this.user.roller_recharge_at;

            const now = Date.now();
            // Mostrar en amarillo (warning) si está en cooldown o si no hay recargas
            if (rechargeAt && (rechargeAt * 1000) > now) return true;
            if (recharges === 0) return true;
            return false;
        },
        isResting(bot) {
            const restUntil = bot === 'chainer' ? this.user.chainer_rest_until : this.user.roller_rest_until;
            return restUntil && (restUntil * 1000) > Date.now();
        },
        getStatusText(bot) {
            const running = bot === 'chainer' ? this.user.chainer_running : this.user.roller_running;
            if (!running) return 'Detenido';

            const restUntil = bot === 'chainer' ? this.user.chainer_rest_until : this.user.roller_rest_until;
            if (restUntil && (restUntil * 1000) > Date.now()) {
                const diff = Math.ceil((restUntil * 1000 - Date.now()) / 1000);
                return `Descansando (${this.formatTime(diff)})`;
            }
            return 'Funcionando';
        },
        getUptime(bot) {
            const startTime = bot === 'chainer' ? this.user.chainer_start_time : this.user.roller_start_time;
            if (!startTime) return '00:00';
            const diff = Math.floor((Date.now() - (startTime * 1000)) / 1000);
            return this.formatTime(diff);
        },
        async toggleBot(type) {
            try {
                const res = await fetch(`${this.apiBase}/toggle_bot`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email: this.user.email, type })
                });
                const data = await res.json();
                if (data.success) {
                    this.user[`${type}_running`] = data.running;
                    if (data.running) {
                        this.user[`${type}_start_time`] = Date.now() / 1000;
                    }
                } else {
                    alert(data.message || 'Error al cambiar estado');
                }
            } catch (err) {
                console.error(err);
            }
        },

        async fetchStatus() {
            if (!this.user || !this.user.email) return;
            try {
                const res = await fetch(`${this.apiBase}/status?email=${this.user.email}`);
                const data = await res.json();
                if (data.success) {
                    // Mezclar datos nuevos con los existentes para no perder el objeto user
                    Object.assign(this.user, data);
                }
            } catch (err) {
                console.error("Error fetching status", err);
            }
        },

        startPolling() {
            if (this.pollingInterval) clearInterval(this.pollingInterval);
            this.fetchStatus();
            this.pollingInterval = setInterval(() => {
                this.fetchStatus();
            }, 2000); // Actualización cada 2 segundos para mayor fluidez
        },

        async buySub(plan) {
            const res = await fetch(`${this.apiBase}/buy_sub`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: this.user.email, plan })
            });
            const data = await res.json();
            if (data.success) {
                if (plan === 'chainer' || plan === 'both') this.user.sub_chainer = true;
                if (plan === 'roller' || plan === 'both') this.user.sub_roller = true;
                this.fetchStatus();
            }
        },

        openCryptoModal(plan) {
            this.selectedPlan = plan;
            this.selectedAmount = plan === 'both' ? 7 : 5;
            this.selectedPlanName = plan === 'both' ? 'Pack Completo' : (plan === 'chainer' ? 'Chainer' : 'Roller');
            this.showCryptoModal = true;
            this.initIcons();
        },

        copyWallet() {
            navigator.clipboard.writeText(this.walletAddr);
            console.log("Wallet copied to clipboard");
        },

        changeView(view) {
            this.currentView = view;
            if (view === 'dashboard' || view === 'settings') {
                this.$nextTick(() => {
                    this.initIcons();
                });
            }
        },

        initIcons() {
            this.$nextTick(() => {
                if (typeof lucide !== 'undefined') lucide.createIcons();
            });
        },

        async verifyCrypto() {
            if (!this.txid || this.txid.length < 20) {
                alert("TXID inválido o muy corto");
                return;
            }
            try {
                const res = await fetch(`${this.apiBase}/submit_payment`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email: this.user.email,
                        plan: this.selectedPlan,
                        txid: this.txid
                    })
                });
                const data = await res.json();
                if (data.success) {
                    alert("Su hash ha sido enviado correctamente. Un administrador lo verificará pronto.");
                    this.showCryptoModal = false;
                    this.txid = '';

                    // Solo para pruebas/demo: automover a activación tras unos segundos
                    setTimeout(() => {
                        this.buySub(this.selectedPlan);
                    }, 5000);
                }
            } catch (err) {
                console.error("Error submitting payment:", err);
            }
        },

        showStatus(text, type = 'success') {
            this.statusMsg = { text, type };
            this.initIcons();
            setTimeout(() => {
                this.statusMsg.text = '';
            }, 3000);
        },


        async updateSettings() {
            try {
                const res = await fetch(`${this.apiBase}/update_settings`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email: this.user.email,
                        chainer_turbo_threshold: this.user.chainer_turbo_threshold,
                        chainer_rest_threshold: this.user.chainer_rest_threshold,
                        chainer_rest_duration: this.user.chainer_rest_duration,
                        roller_turbo_threshold: this.user.roller_turbo_threshold,
                        roller_rest_threshold: this.user.roller_rest_threshold,
                        roller_rest_duration: this.user.roller_rest_duration
                    })
                });
                const data = await res.json();
                if (data.success) {
                    this.showStatus("¡Configuración guardada!", 'success');
                } else {
                    this.showStatus("Error: " + data.message, 'error');
                }
            } catch (err) {
                this.showStatus("Error de conexión", 'error');
            }
        }

    }
}).mount('#app')

