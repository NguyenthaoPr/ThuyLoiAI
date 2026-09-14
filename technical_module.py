<!DOCTYPE html>
<html lang="vi" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>THUY LOI AI - Thông số kỹ thuật</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        darkBg: '#0b1329',
                        darkCard: '#111c38',
                        darkBorder: '#1e294b',
                    }
                }
            }
        }
    </script>
    <!-- Chart.js CDN -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <!-- FontAwesome for Icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        body { font-family: 'Inter', sans-serif; }
    </style>
</head>
<body class="bg-slate-100 dark:bg-darkBg text-slate-800 dark:text-slate-100 min-h-screen transition-colors duration-300">

    <!-- Main Container -->
    <div class="max-w-md mx-auto p-4 space-y-4 pb-12">
        
        <!-- Header Section -->
        <div class="flex items-center justify-between bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-600 dark:text-slate-300 shadow-inner">
                    <i class="fa-solid fa-gear animate-spin-slow"></i>
                </div>
                <div>
                    <h1 class="font-bold text-base leading-tight">THUY LOI AI</h1>
                    <p class="text-xs text-slate-500 dark:text-slate-400">Thông số kỹ thuật · Control Room</p>
                </div>
            </div>
            <div class="flex items-center space-x-2">
                <div class="flex items-center space-x-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-500 text-xs font-medium">
                    <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                    <span>Live</span>
                </div>
                <!-- Theme Toggle Button -->
                <button onclick="toggleTheme()" class="w-9 h-9 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-600 dark:text-amber-400 transition-transform active:scale-95 shadow-sm">
                    <i id="themeIcon" class="fa-solid fa-moon dark:fa-sun"></i>
                </button>
            </div>
        </div>

        <!-- Smart Alert Banner -->
        <div id="alertBanner" class="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 text-xs flex items-center justify-between shadow-sm">
            <div class="flex items-center space-x-2">
                <i class="fa-solid fa-shield-halved text-sm"></i>
                <span id="alertText" class="font-medium">Hệ thống an toàn: Mực nước thấp hơn MNDBT, vận hành bình thường.</span>
            </div>
            <span class="text-[10px] opacity-75">Auto-check</span>
        </div>

        <!-- Control Inputs Section -->
        <div class="bg-white dark:bg-darkCard p-4 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder space-y-3">
            <!-- Công trình -->
            <div>
                <label class="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">Công trình</label>
                <div class="relative">
                    <select id="reservoirSelect" onchange="updateData()" class="w-full bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-darkBorder rounded-xl p-3 text-sm font-semibold appearance-none focus:outline-none focus:ring-2 focus:ring-blue-500">
                        <option value="H8">Hồ Cao Ngạn (H8)</option>
                        <option value="H14" selected>Hồ Đông Tiền (H14)</option>
                    </select>
                    <div class="absolute right-3.5 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400 text-xs">
                        <i class="fa-solid fa-chevron-up-down"></i>
                    </div>
                </div>
            </div>

            <!-- Thông số -->
            <div>
                <label class="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">Thông số</label>
                <div class="relative">
                    <select class="w-full bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-darkBorder rounded-xl p-3 text-sm font-semibold appearance-none focus:outline-none focus:ring-2 focus:ring-blue-500">
                        <option>Mực nước</option>
                    </select>
                    <div class="absolute right-3.5 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400 text-xs">
                        <i class="fa-solid fa-chevron-up-down"></i>
                    </div>
                </div>
            </div>

            <!-- Thời gian -->
            <div>
                <label class="block text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">Thời gian</label>
                <div class="relative">
                    <select class="w-full bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-darkBorder rounded-xl p-3 text-sm font-semibold appearance-none focus:outline-none focus:ring-2 focus:ring-blue-500">
                        <option>7 ngày</option>
                    </select>
                    <div class="absolute right-3.5 top-1/2 -translate-y-1/2 pointer-events-none text-slate-400 text-xs">
                        <i class="fa-solid fa-chevron-up-down"></i>
                    </div>
                </div>
            </div>

            <!-- Làm mới Button -->
            <button onclick="triggerRefresh()" class="w-full bg-gradient-to-r from-blue-600 to-cyan-500 text-white font-medium py-3 rounded-xl shadow-lg shadow-blue-500/20 active:scale-[0.98] transition-all flex items-center justify-center space-x-2 text-sm">
                <i id="refreshIcon" class="fa-solid fa-rotate"></i>
                <span>Làm mới</span>
            </button>
        </div>

        <!-- KPI Grid Cards -->
        <div class="grid grid-cols-2 gap-3">
            <!-- Mực nước hiện tại -->
            <div class="bg-white dark:bg-darkCard p-4 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder relative overflow-hidden flex flex-col justify-between">
                <div class="absolute -right-6 -bottom-6 w-24 h-24 bg-blue-500/5 dark:bg-blue-500/10 rounded-full blur-xl pointer-events-none"></div>
                <div class="flex items-center justify-between mb-2">
                    <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Mực nước hiện tại</span>
                    <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
                </div>
                <div>
                    <div class="text-2xl font-bold tracking-tight" id="valHtl">44,11</div>
                    <div class="text-xs text-slate-400 mt-0.5">m <span class="text-[10px] text-slate-500 block">Lần đo mới nhất</span></div>
                </div>
            </div>

            <!-- Trạng thái -->
            <div class="bg-white dark:bg-darkCard p-4 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder relative overflow-hidden flex flex-col justify-between">
                <div class="absolute -right-6 -bottom-6 w-24 h-24 bg-emerald-500/5 dark:bg-emerald-500/10 rounded-full blur-xl pointer-events-none"></div>
                <div class="flex items-center justify-between mb-2">
                    <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Trạng thái</span>
                    <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
                </div>
                <div>
                    <div class="text-xl font-bold tracking-tight text-emerald-500">Có dữ liệu</div>
                    <div class="text-[11px] text-slate-400 mt-0.5">Cập nhật 07:00:00<br>14/9/2026</div>
                </div>
            </div>

            <!-- MNDBT -->
            <div class="bg-white dark:bg-darkCard p-4 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder relative overflow-hidden flex flex-col justify-between">
                <div class="absolute -right-6 -bottom-6 w-24 h-24 bg-amber-500/5 dark:bg-amber-500/10 rounded-full blur-xl pointer-events-none"></div>
                <div class="flex items-center justify-between mb-2">
                    <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">MNDBT</span>
                    <span class="w-2 h-2 rounded-full bg-amber-500"></span>
                </div>
                <div>
                    <div class="text-2xl font-bold tracking-tight" id="valMndbt">52,35</div>
                    <div class="text-xs text-slate-400 mt-0.5">m <span class="text-[10px] text-slate-500 block">Mực nước dâng bình thường</span></div>
                </div>
            </div>

            <!-- MNDGC -->
            <div class="bg-white dark:bg-darkCard p-4 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder relative overflow-hidden flex flex-col justify-between">
                <div class="absolute -right-6 -bottom-6 w-24 h-24 bg-rose-500/5 dark:bg-rose-500/10 rounded-full blur-xl pointer-events-none"></div>
                <div class="flex items-center justify-between mb-2">
                    <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">MNDGC</span>
                    <span class="w-2 h-2 rounded-full bg-rose-500"></span>
                </div>
                <div>
                    <div class="text-2xl font-bold tracking-tight" id="valMndgc">54,20</div>
                    <div class="text-xs text-slate-400 mt-0.5">m <span class="text-[10px] text-slate-500 block">Mực nước dâng gia cường</span></div>
                </div>
            </div>
        </div>

        <!-- Tổng lượng mưa Card (Full Width) -->
        <div class="bg-white dark:bg-darkCard p-4 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder relative overflow-hidden">
            <div class="flex items-center justify-between mb-2">
                <span class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Tổng lượng mưa</span>
                <span class="w-2 h-2 rounded-full bg-cyan-500"></span>
            </div>
            <div class="text-2xl font-bold tracking-tight" id="valRain">355,00</div>
            <div class="text-xs text-slate-400 mt-0.5">mm</div>
        </div>

        <!-- Biểu đồ diễn biến Section -->
        <div class="bg-white dark:bg-darkCard p-4 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder space-y-3">
            <div class="flex items-center justify-between">
                <div>
                    <h2 class="font-bold text-sm">Biểu đồ diễn biến</h2>
                    <p class="text-[11px] text-slate-400">Mực nước · lượng mưa · MNDBT · MNDGC</p>
                </div>
                <button onclick="resetChartZoom()" class="w-8 h-8 rounded-lg bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-slate-500 hover:text-blue-500 transition-colors">
                    <i class="fa-solid fa-arrow-rotate-left text-xs"></i>
                </button>
            </div>

            <!-- Legend tags -->
            <div class="flex flex-wrap items-center gap-3 text-[11px] text-slate-400 pt-1">
                <div class="flex items-center space-x-1.5"><span class="w-2.5 h-2.5 rounded-full bg-slate-400"></span><span>Lượng mưa</span></div>
                <div class="flex items-center space-x-1.5"><span class="w-2.5 h-2.5 rounded-full bg-blue-500"></span><span>Mực nước</span></div>
                <div class="flex items-center space-x-1.5"><span class="w-2.5 h-2.5 rounded-full bg-amber-500"></span><span>MNDBT</span></div>
                <div class="flex items-center space-x-1.5"><span class="w-2.5 h-2.5 rounded-full bg-rose-500"></span><span>MNDGC</span></div>
            </div>

            <!-- Chart Canvas Container -->
            <div class="relative h-64 w-full pt-2">
                <canvas id="hydroChart"></canvas>
            </div>
        </div>

        <!-- Thông tin công trình Section -->
        <div class="bg-white dark:bg-darkCard p-4 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder space-y-3">
            <div>
                <h2 class="font-bold text-sm">Thông tin công trình</h2>
                <p class="text-[11px] text-slate-400">Khu vực thông tin kỹ thuật</p>
            </div>
            
            <div class="bg-slate-50 dark:bg-slate-800/40 p-3 rounded-xl border border-slate-200 dark:border-darkBorder">
                <span class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1">Công trình đang chọn</span>
                <span id="txtCongTrinh" class="font-bold text-sm">Hồ Đông Tiền (H14)</span>
            </div>

            <div class="bg-slate-50 dark:bg-slate-800/40 p-3 rounded-xl border border-slate-200 dark:border-darkBorder">
                <span class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-2">Trạm mưa</span>
                <div class="flex space-x-2">
                    <span class="px-3 py-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-darkBorder rounded-lg text-xs font-medium shadow-sm">X</span>
                </div>
            </div>
        </div>

        <!-- Tóm tắt kỹ thuật & Xu hướng mực nước -->
        <div class="space-y-3">
            <div>
                <h2 class="font-bold text-sm">Tóm tắt kỹ thuật</h2>
                <p class="text-[11px] text-slate-400">Phân tích so liêu tu du lieu thuc te, khong tu dong gan muc canh bao</p>
            </div>

            <div class="grid grid-cols-2 gap-3">
                <div class="bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder">
                    <span class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider flex items-center space-x-1">
                        <i class="fa-solid fa-arrows-up-down text-blue-500"></i>
                        <span>So với MNDBT</span>
                    </span>
                    <div class="text-xl font-bold mt-1 text-blue-500">-8,24 m</div>
                    <div class="text-[10px] text-slate-400 mt-0.5 leading-tight">Mực nước đang thấp hơn MNDBT</div>
                </div>

                <div class="bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder">
                    <span class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider flex items-center space-x-1">
                        <i class="fa-solid fa-flag text-rose-500"></i>
                        <span>So với MNDGC</span>
                    </span>
                    <div class="text-xl font-bold mt-1 text-slate-700 dark:text-slate-200">-10,09 m</div>
                    <div class="text-[10px] text-slate-400 mt-0.5 leading-tight">Chưa vượt MNDGC</div>
                </div>
            </div>

            <div class="bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder">
                <span class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider flex items-center space-x-1">
                    <i class="fa-solid fa-wave-square text-cyan-500"></i>
                    <span>Biến động gần nhất</span>
                </span>
                <div class="text-xl font-bold mt-1 text-emerald-500">+0,02 m</div>
                <div class="text-[10px] text-slate-400 mt-0.5">So với lần đo liền trước</div>
            </div>

            <!-- Xu hướng mực nước Grid -->
            <div class="pt-2">
                <h3 class="font-bold text-xs uppercase tracking-wider text-slate-400 mb-2">Xu hướng mực nước</h3>
                <div class="grid grid-cols-2 gap-3">
                    <div class="bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder">
                        <div class="text-[10px] text-slate-400 uppercase font-semibold">24 Giờ</div>
                        <div class="text-lg font-bold text-emerald-500 mt-0.5">+0,06 m</div>
                        <div class="text-[10px] text-slate-400">Tăng · 5 lần đo</div>
                    </div>
                    <div class="bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder">
                        <div class="text-[10px] text-slate-400 uppercase font-semibold">3 Ngày</div>
                        <div class="text-lg font-bold text-emerald-500 mt-0.5">+1,13 m</div>
                        <div class="text-[10px] text-slate-400">Tăng · 13 lần đo</div>
                    </div>
                    <div class="bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder">
                        <div class="text-[10px] text-slate-400 uppercase font-semibold">7 Ngày</div>
                        <div class="text-lg font-bold text-emerald-500 mt-0.5">+1,52 m</div>
                        <div class="text-[10px] text-slate-400">Tăng · 28 lần đo</div>
                    </div>
                    <div class="bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder">
                        <div class="text-[10px] text-slate-400 uppercase font-semibold">Thấp nhất</div>
                        <div class="text-lg font-bold mt-0.5">42,59 m</div>
                        <div class="text-[10px] text-slate-400">Trong khoảng đang chọn</div>
                    </div>
                </div>
                <div class="mt-3 bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder">
                    <div class="text-[10px] text-slate-400 uppercase font-semibold">Cao nhất</div>
                    <div class="text-lg font-bold mt-0.5">44,11 m</div>
                    <div class="text-[10px] text-slate-400">Trong khoảng đang chọn</div>
                </div>
            </div>

            <!-- Lượng mưa theo từng chuỗi -->
            <div class="bg-white dark:bg-darkCard p-3 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder mt-3">
                <span class="text-[10px] font-semibold text-slate-400 uppercase tracking-wider block mb-1">Lượng mưa theo từng chuỗi</span>
                <div class="text-sm font-semibold">X (mm): 355,00 mm</div>
            </div>
        </div>

        <!-- Dữ liệu gần nhất Section -->
        <div class="bg-white dark:bg-darkCard p-4 rounded-2xl shadow-sm border border-slate-200 dark:border-darkBorder space-y-3">
            <div>
                <h2 class="font-bold text-sm">Dữ liệu gần nhất</h2>
                <p class="text-[11px] text-slate-400">Dữ liệu thực tế từ AI_DATA qua Apps Script API</p>
            </div>

            <!-- Search Bar & Export button -->
            <div class="space-y-2">
                <div class="relative">
                    <span class="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400 text-xs">
                        <i class="fa-solid fa-magnifying-glass"></i>
                    </span>
                    <input type="text" id="searchInput" onkeyup="filterTable()" placeholder="Tìm nhanh ngày, thông số, giá trị..." class="w-full bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-darkBorder rounded-xl pl-9 pr-3 py-2.5 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500">
                </div>
                <div class="flex items-center justify-between text-xs text-slate-400 px-1">
                    <span id="recordCount">44 bản ghi · trang 1/5</span>
                    <button onclick="exportData()" class="text-blue-500 font-medium hover:underline flex items-center space-x-1">
                        <i class="fa-solid fa-download text-[10px]"></i>
                        <span>Xuất CSV</span>
                    </button>
                </div>
            </div>

            <button onclick="clearData()" class="px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-rose-500/10 hover:text-rose-500 rounded-lg text-xs font-medium transition-colors">
                Xóa bộ lọc
            </button>

            <!-- Data List Cards -->
            <div id="dataListContainer" class="space-y-2.5 pt-1">
                <div class="bg-slate-50 dark:bg-slate-800/40 p-3 rounded-xl border border-slate-200 dark:border-darkBorder">
                    <div class="text-[10px] text-slate-400">14/9/2026 · 07:00</div>
                    <div class="font-bold text-sm mt-0.5">HTL (m)</div>
                    <div class="text-base font-extrabold text-blue-500">44,11 m</div>
                </div>
                <div class="bg-slate-50 dark:bg-slate-800/40 p-3 rounded-xl border border-slate-200 dark:border-darkBorder">
                    <div class="text-[10px] text-slate-400">14/9/2026 · 07:00</div>
                    <div class="font-bold text-sm mt-0.5">X (mm)</div>
                    <div class="text-base font-extrabold text-cyan-500">4,00 mm</div>
                </div>
                <div class="bg-slate-50 dark:bg-slate-800/40 p-3 rounded-xl border border-slate-200 dark:border-darkBorder">
                    <div class="text-[10px] text-slate-400">14/9/2026 · 01:00</div>
                    <div class="font-bold text-sm mt-0.5">HTL (m)</div>
                    <div class="text-base font-extrabold text-blue-500">44,09 m</div>
                </div>
                <div class="bg-slate-50 dark:bg-slate-800/40 p-3 rounded-xl border border-slate-200 dark:border-darkBorder">
                    <div class="text-[10px] text-slate-400">13/9/2026 · 19:00</div>
                    <div class="font-bold text-sm mt-0.5">HTL (m)</div>
                    <div class="text-base font-extrabold text-blue-500">44,08 m</div>
                </div>
            </div>

            <!-- Pagination -->
            <div class="flex items-center justify-center space-x-1.5 pt-2">
                <button class="w-8 h-8 rounded-lg bg-blue-500 text-white font-bold text-xs shadow-sm">1</button>
                <button class="w-8 h-8 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 font-medium text-xs">2</button>
                <button class="w-8 h-8 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 font-medium text-xs">3</button>
                <button class="w-8 h-8 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 font-medium text-xs">4</button>
                <button class="w-8 h-8 rounded-lg bg-slate-100 dark:bg-slate-800 text-slate-500 font-medium text-xs">5</button>
            </div>
        </div>

        <!-- Footer Info -->
        <div class="text-center text-[10px] text-slate-400 pt-4 leading-relaxed">
            THUY LOI AI · Technical Module V1.6 · Apps Script Proxy ·<br>Dashboard kỹ thuật
        </div>

    </div>

    <!-- JavaScript logic -->
    <script>
        // Theme Toggle
        function toggleTheme() {
            const html = document.documentElement;
            if (html.classList.contains('dark')) {
                html.classList.remove('dark');
                localStorage.theme = 'light';
            } else {
                html.classList.add('dark');
                localStorage.theme = 'dark';
            }
        }

        // Initialize theme based on preference
        if (localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
            document.documentElement.classList.add('dark');
        } else {
            document.documentElement.classList.remove('dark');
        }

        // Refresh animation & simulation
        function triggerRefresh() {
            const icon = document.getElementById('refreshIcon');
            icon.classList.add('fa-spin');
            setTimeout(() => {
                icon.classList.remove('fa-spin');
                alert('Đã đồng bộ dữ liệu mới nhất từ Google Sheets API thành công!');
            }, 800);
        }

        // Reservoir change handler simulation
        function updateData() {
            const select = document.getElementById('reservoirSelect');
            const txt = document.getElementById('txtCongTrinh');
            if (select.value === 'H8') {
                txt.textContent = 'Hồ Cao Ngạn (H8)';
                document.getElementById('valHtl').textContent = '51,00';
                document.getElementById('valMndbt').textContent = '61,45';
                document.getElementById('valMndgc').textContent = '63,20';
                document.getElementById('valRain').textContent = '341,00';
            } else {
                txt.textContent = 'Hồ Đông Tiền (H14)';
                document.getElementById('valHtl').textContent = '44,11';
                document.getElementById('valMndbt').textContent = '52,35';
                document.getElementById('valMndgc').textContent = '54,20';
                document.getElementById('valRain').textContent = '355,00';
            }
        }

        function clearData() {
            document.getElementById('searchInput').value = '';
            filterTable();
        }

        function filterTable() {
            const input = document.getElementById('searchInput').value.toLowerCase();
            const container = document.getElementById('dataListContainer');
            const items = container.getElementsByClassName('border');
            
            for (let i = 0; i < items.length; i++) {
                const text = items[i].innerText.toLowerCase();
                if (text.includes(input)) {
                    items[i].style.display = "";
                } else {
                    items[i].style.display = "none";
                }
            }
        }

        function exportData() {
            alert('Đã xuất file báo cáo dữ liệu ca trực dạng CSV thành công!');
        }

        // Chart Initialization using Chart.js
        const ctx = document.getElementById('hydroChart').getContext('2d');
        
        const labels = ['Sep 8', 'Sep 9', 'Sep 10', 'Sep 11', 'Sep 12', 'Sep 13', 'Sep 14'];
        const waterLevelData = [42.1, 42.2, 42.3, 42.5, 42.9, 43.8, 44.11];
        const rainData = [5, 12, 8, 15, 35, 70, 4];
        
        const mndbtLine = Array(labels.length).fill(52.35);
        const mndgcLine = Array(labels.length).fill(54.20);

        const hydroChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Lượng mưa (mm)',
                        data: rainData,
                        type: 'bar',
                        backgroundColor: 'rgba(148, 163, 184, 0.4)',
                        borderColor: 'transparent',
                        borderRadius: 4,
                        yAxisID: 'y1',
                        order: 2
                    },
                    {
                        label: 'Mực nước (m)',
                        data: waterLevelData,
                        borderColor: '#3b82f6',
                        backgroundColor: '#3b82f6',
                        borderWidth: 2.5,
                        pointRadius: 3,
                        tension: 0.2,
                        yAxisID: 'y',
                        order: 1
                    },
                    {
                        label: 'MNDBT',
                        data: mndbtLine,
                        borderColor: '#f59e0b',
                        borderWidth: 1.5,
                        borderDash: [5, 5],
                        pointRadius: 0,
                        yAxisID: 'y',
                        order: 3
                    },
                    {
                        label: 'MNDGC',
                        data: mndgcLine,
                        borderColor: '#f43f5e',
                        borderWidth: 1.5,
                        borderDash: [5, 5],
                        pointRadius: 0,
                        yAxisID: 'y',
                        order: 4
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        backgroundColor: '#111c38',
                        titleColor: '#fff',
                        bodyColor: '#cbd5e1',
                        borderColor: '#1e294b',
                        borderWidth: 1
                    }
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { color: '#94a3b8', font: { size: 10 } }
                    },
                    y: {
                        type: 'linear',
                        display: true,
                        position: 'left',
                        min: 40,
                        max: 56,
                        grid: { color: 'rgba(148, 163, 184, 0.1)' },
                        ticks: { color: '#94a3b8', font: { size: 10 } }
                    },
                    y1: {
                        type: 'linear',
                        display: true,
                        position: 'right',
                        min: 0,
                        max: 80,
                        grid: { display: false },
                        ticks: { color: '#94a3b8', font: { size: 10 } }
                    }
                }
            }
        });

        function resetChartZoom() {
            hydroChart.resetZoom ? hydroChart.resetZoom() : hydroChart.update();
        }
    </script>
</body>
</html>
