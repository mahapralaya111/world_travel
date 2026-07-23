/**
 * 国际旅游规划助手 - 前端 API 客户端
 * 统一封装后端 REST 接口：认证、行程、批注、路线、城市、游记、统计
 * 所有页面共用此模块
 */
(function (global) {
    'use strict';

    const TOKEN_KEY = 'itp_token';
    const USER_KEY = 'itp_user';
    const TRIP_KEY = 'itp_current_trip';

    const API = {
        // 后端地址（与页面同源时留空；前后端分离开发时可改为 http://127.0.0.1:5000/api）
        base: '',

        /* ---------- 会话状态 ---------- */
        get token() { return localStorage.getItem(TOKEN_KEY) || ''; },
        set token(v) { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); },
        get user() {
            try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); }
            catch (e) { return null; }
        },
        set user(v) { v ? localStorage.setItem(USER_KEY, JSON.stringify(v)) : localStorage.removeItem(USER_KEY); },
        isLoggedIn() { return !!this.token; },

        /* ---------- 当前行程 ---------- */
        get currentTripId() { return localStorage.getItem(TRIP_KEY) || ''; },
        set currentTripId(v) { v ? localStorage.setItem(TRIP_KEY, v) : localStorage.removeItem(TRIP_KEY); },

        /* ---------- 基础请求 ---------- */
        async request(path, options = {}) {
            const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
            if (this.token) headers['Authorization'] = 'Bearer ' + this.token;
            const resp = await fetch(this.base + path, Object.assign({}, options, { headers }));
            let data = null;
            try { data = await resp.json(); }
            catch (e) { data = { code: -1, msg: '服务响应异常' }; }
            if (resp.status === 401) {
                // token 失效自动登出
                this.logout();
                if (global.location && global.location.pathname !== '/pages/login.html') {
                    // 不强制跳转，交由页面自行处理
                }
            }
            return data;
        },
        get(path) { return this.request(path); },
        post(path, body) { return this.request(path, { method: 'POST', body: JSON.stringify(body || {}) }); },
        put(path, body) { return this.request(path, { method: 'PUT', body: JSON.stringify(body || {}) }); },
        del(path) { return this.request(path, { method: 'DELETE' }); },

        /* ---------- 认证 ---------- */
        register(data) { return this.post('/api/auth/register', data); },
        login(data) { return this.post('/api/auth/login', data); },
        me() { return this.get('/api/auth/me'); },
        updateProfile(data) { return this.post('/api/auth/update', data); },
        logout() { this.token = ''; this.user = null; },

        /* ---------- 行程 ---------- */
        listTrips() { return this.get('/api/trips'); },
        getTrip(id) { return this.get('/api/trips/' + id); },
        createTrip(data) { return this.post('/api/trips', data); },
        updateTrip(id, data) { return this.put('/api/trips/' + id, data); },
        deleteTrip(id) { return this.del('/api/trips/' + id); },
        syncCountry(tripId, payload) { return this.post('/api/trips/' + tripId + '/sync', payload); },

        /* ---------- 城市 ---------- */
        searchCities(q, opts) {
            const params = new URLSearchParams({ q: q || '' });
            if (opts && opts.limit) params.set('limit', opts.limit);
            if (opts && opts.include_sights) params.set('include_sights', '1');
            return this.get('/api/cities/search?' + params.toString());
        },
        listCountries() { return this.get('/api/cities/countries'); },

        /* ---------- 游记 ---------- */
        listNotes(params) {
            const p = new URLSearchParams(params || {});
            return this.get('/api/notes?' + p.toString());
        },
        myNotes() { return this.get('/api/notes/mine'); },
        getNote(id) { return this.get('/api/notes/' + id); },
        createNote(data) { return this.post('/api/notes', data); },
        updateNote(id, data) { return this.put('/api/notes/' + id, data); },
        deleteNote(id) { return this.del('/api/notes/' + id); },

        /* ---------- 统计 ---------- */
        myStats() { return this.get('/api/stats/mine'); },
        globalStats() { return this.get('/api/stats/global'); },
        achievements() { return this.get('/api/stats/achievements'); },
        tripCompletion() { return this.get('/api/stats/trip-completion'); },

        /* ---------- 收藏 ---------- */
        listFavorites(itemType) {
            const p = itemType ? '?item_type=' + itemType : '';
            return this.get('/api/favorites' + p);
        },
        addFavorite(data) { return this.post('/api/favorites', data); },
        removeFavorite(id) { return this.del('/api/favorites/' + id); },
        checkFavorites(keys) { return this.get('/api/favorites/check?keys=' + encodeURIComponent(keys.join(','))); },

        /* ---------- 智能推荐 ---------- */
        recommend(itemType, limit) {
            return this.get('/api/recommend?item_type=' + (itemType || 'sight') + '&limit=' + (limit || 8));
        },

        /* ---------- 智能路线规划 ---------- */
        planRoute(points) {
            return this.post('/api/plan/route', points ? { points } : {});
        },

        /* ---------- AI 智能出行规划 ---------- */
        aiStatus() { return this.get('/api/ai/status'); },
        aiPlan(data) { return this.post('/api/ai/plan', data); },

        /* ---------- 行程规划引擎 ---------- */
        planGenerate(data) { return this.post('/api/plan/generate', data); },
        planSchema() { return this.get('/api/plan/schema'); },
        getTripPlan(tripId) { return this.get('/api/trips/' + tripId + '/plan'); },
        saveTripPlan(tripId, plan) { return this.put('/api/trips/' + tripId + '/plan', { plan }); },
        applyIntent(tripId, data) { return this.post('/api/trips/' + tripId + '/plan/intent', data); },
        buildOffline(plan, tripId) { return this.post('/api/plan/offline', { plan, trip_id: tripId || null }); },
        offlineFromTrip(tripId) { return this.get('/api/plan/offline/' + tripId); },
        planMarkdown(tripId) { return this.get('/api/plan/markdown/' + tripId); },
        // 分享/导入
        createShare(payload) { return this.post('/api/shares', payload); },
        listShares(query) { return this.get('/api/shares' + (query ? ('?page=' + (query.page || 1) + '&size=' + (query.size || 20)) : '')); },
        revokeShare(shareId) { return this.del('/api/shares/' + shareId); },
        inspectShare(token) { return this.get('/api/shares/' + token); },
        importShare(token, payload) { return this.post('/api/shares/' + token + '/import', payload || {}); },

        /* ---------- 前端运行时配置（是否启用高德 / AI provider） ---------- */
        frontendConfig() { return this.get('/api/config/frontend'); },

        /* ---------- 天气 & POI（双地图方案：境内高德 / 境外 OSM+Open-Meteo） ---------- */
        getWeather(lat, lng, days) {
            const q = new URLSearchParams({ lat: lat, lng: lng });
            if (days) q.set('days', days);
            return this.get('/api/weather?' + q.toString());
        },
        getPoiNearby(lat, lng, radius, limit) {
            const q = new URLSearchParams({ lat: lat, lng: lng });
            if (radius != null) q.set('radius', radius);
            if (limit != null) q.set('limit', limit);
            return this.get('/api/poi/nearby?' + q.toString());
        },
        getPoiDetail(lat, lng, name) {
            const q = new URLSearchParams({ lat: lat, lng: lng, name: name || '' });
            return this.get('/api/poi/detail?' + q.toString());
        },
    };

    global.API = API;
})(window);
