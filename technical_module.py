// ============================================================
// THUY LOI AI - TECHNICAL API GATEWAY V1.0
// ============================================================
// MỤC ĐÍCH:
// - API ĐỌC RIÊNG cho module "Thông số kỹ thuật".
// - Đọc trực tiếp sheet AI_DATA.
// - KHÔNG ghi / sửa / xóa AI_DATA.
// - KHÔNG thay thế Apps Script hiện tại của THỦY LỢI AI.
// - Có thể triển khai thành một Web App Apps Script RIÊNG.
//
// KIẾN TRÚC:
// Google Sheet AI_DATA
//        │
//        ▼
// Technical API (Code.gs này)
//        │
//        ├── api=facilities
//        ├── api=parameters
//        └── api=chart
//        │
//        ▼
// technical_module.py
//
// API hỗ trợ thêm kiểu truy vấn cũ:
// ?cong_trinh=...&thong_so=...&ngay=...&gio=...
// để không làm mất khả năng tương thích.
// ============================================================

const TECHNICAL_CONFIG = {
  // ID Google Sheet THỦY LỢI AI - DATA
  SPREADSHEET_ID: '1SJU9aCRZGWeAeHw6UfY_08HK8-A34kIlnrEiPJNEnko',

  AI_SHEET_NAME: 'AI_DATA',

  CACHE_SECONDS: 30,

  VERSION: 'TECHNICAL-API-V1.0'
};

// ============================================================
// 01. RESPONSE ENGINE
// ============================================================

function jsonResponse_(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

function successResponse_(api, data, extra) {
  const result = {
    ok: true,
    api: api,
    data: data,
    version: TECHNICAL_CONFIG.VERSION,
    timestamp: new Date().toISOString()
  };

  if (extra && typeof extra === 'object') {
    Object.keys(extra).forEach(function(key) {
      result[key] = extra[key];
    });
  }

  return result;
}

function errorResponse_(api, error, details) {
  const result = {
    ok: false,
    api: api || '',
    error: String(error || 'Lỗi không xác định'),
    version: TECHNICAL_CONFIG.VERSION,
    timestamp: new Date().toISOString()
  };

  if (details) {
    result.details = details;
  }

  return result;
}

// ============================================================
// 02. QUERY PARAMETER ENGINE
// ============================================================
// Apps Script chuẩn đã có e.parameter.
// Hàm này có thêm lớp bảo vệ cho trường hợp hệ thống triển khai
// hoặc URL trung gian truyền toàn bộ query string vào biến api.
// Ví dụ lỗi trước đây:
// api = "parameters&facility=Hồ Đồng Tiến (H14)"
// ============================================================

function parseQueryString_(queryString) {
  const result = {};

  if (!queryString) {
    return result;
  }

  String(queryString)
    .split('&')
    .forEach(function(part) {
      if (!part) return;

      const equalIndex = part.indexOf('=');
      let key = equalIndex >= 0 ? part.substring(0, equalIndex) : part;
      let value = equalIndex >= 0 ? part.substring(equalIndex + 1) : '';

      try {
        key = decodeURIComponent(key.replace(/\+/g, ' '));
      } catch (err) {
        key = key.replace(/\+/g, ' ');
      }

      try {
        value = decodeURIComponent(value.replace(/\+/g, ' '));
      } catch (err) {
        value = value.replace(/\+/g, ' ');
      }

      if (key) {
        result[key] = value;
      }
    });

  return result;
}

function getRequestParams_(e) {
  const params = {};

  if (e && e.parameter) {
    Object.keys(e.parameter).forEach(function(key) {
      params[key] = e.parameter[key];
    });
  }

  // Bổ sung từ queryString nếu có.
  if (e && e.queryString) {
    const parsed = parseQueryString_(e.queryString);
    Object.keys(parsed).forEach(function(key) {
      if (params[key] === undefined || params[key] === '') {
        params[key] = parsed[key];
      }
    });
  }

  // KHẮC PHỤC TRỰC TIẾP TRƯỜNG HỢP:
  // api = "parameters&facility=..."
  const rawApi = String(params.api || '').trim();

  if (rawApi.indexOf('&') >= 0) {
    const apiParts = parseQueryString_(rawApi);
    const firstPart = rawApi.split('&')[0];

    params.api = firstPart;

    Object.keys(apiParts).forEach(function(key) {
      if (key !== firstPart && (params[key] === undefined || params[key] === '')) {
        params[key] = apiParts[key];
      }
    });
  }

  // Một số hệ thống có thể truyền action thay vì api.
  if (!params.api && params.action) {
    params.api = params.action;
  }

  return params;
}

function clean_(value) {
  return String(value === undefined || value === null ? '' : value).trim();
}

// ============================================================
// 03. DATA SOURCE
// ============================================================

function getAIDataSheet_() {
  const ss = SpreadsheetApp.openById(TECHNICAL_CONFIG.SPREADSHEET_ID);
  const sheet = ss.getSheetByName(TECHNICAL_CONFIG.AI_SHEET_NAME);

  if (!sheet) {
    throw new Error(
      'Không tìm thấy sheet ' + TECHNICAL_CONFIG.AI_SHEET_NAME
    );
  }

  return sheet;
}

function getAIData_() {
  const cache = CacheService.getScriptCache();
  const cacheKey = 'TECHNICAL_AI_DATA_V1';

  const cached = cache.get(cacheKey);
  if (cached) {
    try {
      return JSON.parse(cached);
    } catch (err) {
      // Cache lỗi thì đọc lại sheet.
    }
  }

  const sheet = getAIDataSheet_();
  const data = sheet.getDataRange().getDisplayValues();

  if (!data || data.length < 2) {
    throw new Error('AI_DATA chưa có dữ liệu.');
  }

  // Cache chỉ khi kích thước hợp lý.
  // Nếu dữ liệu quá lớn, không ép cache để tránh lỗi giới hạn cache.
  try {
    const text = JSON.stringify(data);
    if (text.length < 900000) {
      cache.put(cacheKey, text, TECHNICAL_CONFIG.CACHE_SECONDS);
    }
  } catch (err) {
    // Không làm hỏng API chỉ vì cache.
  }

  return data;
}

// ============================================================
// 04. HEADER / COLUMN ENGINE
// ============================================================

function buildColumnMap_(headers) {
  const map = {};

  headers.forEach(function(header, index) {
    const key = clean_(header);
    if (key) {
      map[key] = index;
    }
  });

  return map;
}

function requireColumns_(map) {
  const required = [
    'Ngày',
    'Giờ',
    'Công trình',
    'Thông số',
    'Đơn vị đo',
    'Giá trị'
  ];

  const missing = required.filter(function(name) {
    return map[name] === undefined;
  });

  if (missing.length) {
    throw new Error(
      'AI_DATA thiếu cột: ' + missing.join(', ')
    );
  }
}

// ============================================================
// 05. NORMALIZATION
// ============================================================

function normalizeText_(value) {
  return clean_(value)
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd')
    .replace(/\s+/g, ' ')
    .trim();
}

function stripUnit_(value) {
  return normalizeText_(value)
    .replace(/\s*\([^)]*\)\s*$/g, '')
    .trim();
}

function numberValue_(value) {
  if (value === null || value === undefined) {
    return null;
  }

  let text = clean_(value);
  if (!text) return null;

  // Hỗ trợ số dạng Việt Nam: 1.234,56 và số dạng 1234.56.
  text = text.replace(/\s/g, '');

  if (text.indexOf(',') >= 0 && text.indexOf('.') >= 0) {
    if (text.lastIndexOf(',') > text.lastIndexOf('.')) {
      text = text.replace(/\./g, '').replace(',', '.');
    } else {
      text = text.replace(/,/g, '');
    }
  } else if (text.indexOf(',') >= 0) {
    text = text.replace(',', '.');
  }

  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

function normalizeDay_(value) {
  const text = clean_(value);
  if (!text) return null;

  const n = Number(text);
  if (Number.isFinite(n)) return Math.trunc(n);

  const match = text.match(/(\d{1,2})/);
  return match ? Number(match[1]) : null;
}

function normalizeHour_(value) {
  const text = clean_(value);
  if (!text) return null;

  const match = text.match(/\d{1,2}/);
  if (!match) return null;

  const hour = Number(match[0]);
  return Number.isFinite(hour) ? hour : null;
}

function pad2_(n) {
  return String(n).padStart(2, '0');
}

// ============================================================
// 06. FACILITIES API
// ============================================================

function getFacilities_() {
  const data = getAIData_();
  const map = buildColumnMap_(data[0]);
  requireColumns_(map);

  const colFacility = map['Công trình'];
  const set = {};

  for (let r = 1; r < data.length; r++) {
    const name = clean_(data[r][colFacility]);
    if (name) {
      set[name] = true;
    }
  }

  return Object.keys(set).sort(function(a, b) {
    return a.localeCompare(b, 'vi');
  });
}

// ============================================================
// 07. PARAMETER API
// ============================================================

function classifyParameter_(parameter, unit) {
  const p = normalizeText_(parameter);
  const u = normalizeText_(unit);

  // Mực nước / cao trình mực nước.
  const water =
    p === 'h' ||
    p === 'muc nuoc' ||
    p === 'htl' ||
    p === 'hhl' ||
    p === 'nnm' ||
    p.indexOf('muc nuoc ') === 0;

  // Lượng mưa: chỉ nhận diện các trường hợp rõ ràng.
  // Không coi X (m) hoặc X độ mở là mưa.
  const rainfall =
    p === 'mua' ||
    p === 'luong mua' ||
    p.indexOf('luong mua ') === 0 ||
    p.indexOf('mua ') === 0 ||
    (/^x\b/.test(p) && u.indexOf('mm') >= 0) ||
    p.indexOf('x24') === 0 && u.indexOf('mm') >= 0;

  return {
    water: water,
    rainfall: rainfall
  };
}

function getFacilityParameters_(facility) {
  const requested = clean_(facility);
  if (!requested) {
    throw new Error('Thiếu tên công trình.');
  }

  const data = getAIData_();
  const map = buildColumnMap_(data[0]);
  requireColumns_(map);

  const colFacility = map['Công trình'];
  const colParameter = map['Thông số'];
  const colUnit = map['Đơn vị đo'];

  const keyFacility = normalizeText_(requested);
  const waterSet = {};
  const rainSet = {};
  const otherSet = {};

  for (let r = 1; r < data.length; r++) {
    const rowFacility = normalizeText_(data[r][colFacility]);

    const matched =
      rowFacility === keyFacility ||
      rowFacility.indexOf(keyFacility + ' (') === 0 ||
      rowFacility.indexOf(keyFacility + ' -') === 0;

    if (!matched) continue;

    const parameter = clean_(data[r][colParameter]);
    const unit = clean_(data[r][colUnit]);
    if (!parameter) continue;

    const type = classifyParameter_(parameter, unit);

    if (type.water) {
      waterSet[parameter] = true;
    } else if (type.rainfall) {
      rainSet[parameter] = true;
    } else {
      otherSet[parameter] = true;
    }
  }

  return {
    facility: requested,
    waterLevel: Object.keys(waterSet).sort(function(a, b) {
      return a.localeCompare(b, 'vi');
    }),
    rainfall: Object.keys(rainSet).sort(function(a, b) {
      return a.localeCompare(b, 'vi');
    }),
    other: Object.keys(otherSet).sort(function(a, b) {
      return a.localeCompare(b, 'vi');
    })
  };
}

// ============================================================
// 08. DATE ENGINE
// ============================================================

function parseDateInput_(value) {
  const text = clean_(value);
  if (!text) return null;

  // yyyy-mm-dd
  let match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (match) {
    return new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3])
    );
  }

  // dd/mm/yyyy
  match = text.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})$/);
  if (match) {
    return new Date(
      Number(match[3]),
      Number(match[2]) - 1,
      Number(match[1])
    );
  }

  return null;
}

function dateKey_(year, month, day) {
  return year + '-' + pad2_(month) + '-' + pad2_(day);
}

function rowDate_(row, map, fallbackYear) {
  const day = normalizeDay_(row[map['Ngày']]);
  const month = map['Tháng'] !== undefined
    ? normalizeDay_(row[map['Tháng']])
    : null;

  if (!day || !month) return null;

  const year = Number(fallbackYear) || new Date().getFullYear();
  return new Date(year, month - 1, day);
}

function dateOnly_(date) {
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate()
  );
}

function daysBetween_(a, b) {
  const ms = dateOnly_(b).getTime() - dateOnly_(a).getTime();
  return Math.round(ms / 86400000);
}

// ============================================================
// 09. WATER PARAMETER SELECTION
// ============================================================
// Không tự cộng HTL/HHL/H.
// Nếu người dùng không chọn cụ thể, ưu tiên một chuỗi đại diện:
// H -> Mực nước -> NNM -> HTL -> HHL.
// ============================================================

function chooseWaterParameter_(rows, requested) {
  if (requested) {
    return requested;
  }

  const priority = [
    'H',
    'Mực nước',
    'NNM',
    'HTL',
    'HHL'
  ];

  const available = {};
  rows.forEach(function(item) {
    available[item.parameter] = true;
  });

  for (let i = 0; i < priority.length; i++) {
    if (available[priority[i]]) {
      return priority[i];
    }
  }

  // Nếu tên có đơn vị hoặc cách ghi khác, thử theo normalized key.
  const normalizedPriority = priority.map(normalizeText_);
  for (let i = 0; i < rows.length; i++) {
    const key = normalizeText_(rows[i].parameter);
    if (normalizedPriority.indexOf(key) >= 0) {
      return rows[i].parameter;
    }
  }

  return '';
}

// ============================================================
// 10. TECHNICAL LIMITS
// ============================================================

function findLimit_(rows, names) {
  const keys = names.map(normalizeText_);

  for (let i = 0; i < rows.length; i++) {
    const p = normalizeText_(rows[i].parameter);
    if (keys.indexOf(p) >= 0) {
      const n = numberValue_(rows[i].value);
      if (n !== null) return n;
    }
  }

  return null;
}

// ============================================================
// 11. CHART DATA ENGINE
// ============================================================

function getChartData_(request) {
  const facility = clean_(request.facility);
  if (!facility) {
    throw new Error('Thiếu facility.');
  }

  const requestedYear = Number(request.year) || new Date().getFullYear();
  const requestedDays = Math.max(1, Number(request.days) || 7);
  const requestedWater = clean_(request.waterParameter);
  const rainfallParameterText = clean_(request.rainfallParameters);
  const requestedRainfall = rainfallParameterText
    ? rainfallParameterText.split(',').map(function(x) { return clean_(x); }).filter(Boolean)
    : [];

  const data = getAIData_();
  const map = buildColumnMap_(data[0]);
  requireColumns_(map);

  const colFacility = map['Công trình'];
  const colParameter = map['Thông số'];
  const colUnit = map['Đơn vị đo'];
  const colValue = map['Giá trị'];

  const keyFacility = normalizeText_(facility);
  const rows = [];

  // ----------------------------------------------------------
  // A. Đọc các dòng đúng công trình + đúng năm.
  // ----------------------------------------------------------
  for (let r = 1; r < data.length; r++) {
    const row = data[r];
    const rowFacility = normalizeText_(row[colFacility]);

    const matched =
      rowFacility === keyFacility ||
      rowFacility.indexOf(keyFacility + ' (') === 0 ||
      rowFacility.indexOf(keyFacility + ' -') === 0;

    if (!matched) continue;

    const date = rowDate_(row, map, requestedYear);
    if (!date || date.getFullYear() !== requestedYear) continue;

    const hour = normalizeHour_(row[map['Giờ']]);
    const parameter = clean_(row[colParameter]);
    const unit = clean_(row[colUnit]);
    const value = numberValue_(row[colValue]);

    if (!parameter || value === null) continue;

    rows.push({
      date: date,
      hour: hour === null ? 0 : hour,
      parameter: parameter,
      unit: unit,
      value: value
    });
  }

  if (!rows.length) {
    return {
      facility: facility,
      from: null,
      to: null,
      water: [],
      rainfall: [],
      rainfallPoints: [],
      totalRainfall: null,
      rainfallTotalsByParameter: {},
      limits: {
        mndbt: null,
        mndgc: null
      },
      count: 0,
      updatedAt: null
    };
  }

  // ----------------------------------------------------------
  // B. Xác định khoảng thời gian.
  // ----------------------------------------------------------
  let fromDate = parseDateInput_(request.fromDate);
  let toDate = parseDateInput_(request.toDate);

  const allDates = rows.map(function(item) { return dateOnly_(item.date); });
  const maxDate = new Date(Math.max.apply(null, allDates.map(function(d) { return d.getTime(); })));
  const minDate = new Date(Math.min.apply(null, allDates.map(function(d) { return d.getTime(); })));

  if (!toDate) {
    toDate = maxDate;
  } else {
    toDate = dateOnly_(toDate);
  }

  if (!fromDate) {
    fromDate = new Date(toDate.getTime() - (requestedDays - 1) * 86400000);
  } else {
    fromDate = dateOnly_(fromDate);
  }

  // Không cho khoảng yêu cầu vượt ra ngoài năm đang chọn.
  const yearStart = new Date(requestedYear, 0, 1);
  const yearEnd = new Date(requestedYear, 11, 31);
  if (fromDate < yearStart) fromDate = yearStart;
  if (toDate > yearEnd) toDate = yearEnd;

  // ----------------------------------------------------------
  // C. Lọc theo khoảng ngày.
  // ----------------------------------------------------------
  const inRange = rows.filter(function(item) {
    const d = dateOnly_(item.date);
    return d >= fromDate && d <= toDate;
  });

  // ----------------------------------------------------------
  // D. Tách nhóm mực nước / mưa / giới hạn.
  // ----------------------------------------------------------
  const waterRows = [];
  const rainfallRows = [];
  const limitRows = [];

  inRange.forEach(function(item) {
    const p = normalizeText_(item.parameter);
    const type = classifyParameter_(item.parameter, item.unit);

    if (p === 'mndbt' || p === 'mndgc') {
      limitRows.push(item);
      return;
    }

    if (type.water) {
      waterRows.push(item);
      return;
    }

    if (type.rainfall) {
      rainfallRows.push(item);
    }
  });

  // Giới hạn kỹ thuật nên lấy toàn bộ dữ liệu công trình trong năm,
  // vì có thể MNDBT/MNDGC không lặp lại ở từng ngày.
  const allLimitRows = rows.filter(function(item) {
    const p = normalizeText_(item.parameter);
    return p === 'mndbt' || p === 'mndgc';
  });

  const mndbt = findLimit_(allLimitRows, ['MNDBT']);
  const mndgc = findLimit_(allLimitRows, ['MNDGC']);

  // ----------------------------------------------------------
  // E. Chọn chuỗi mực nước.
  // ----------------------------------------------------------
  const selectedWaterParameter = chooseWaterParameter_(waterRows, requestedWater);

  const selectedWaterRows = selectedWaterParameter
    ? waterRows.filter(function(item) {
        return normalizeText_(item.parameter) === normalizeText_(selectedWaterParameter);
      })
    : [];

  selectedWaterRows.sort(function(a, b) {
    return a.date.getTime() + a.hour * 3600000 -
           (b.date.getTime() + b.hour * 3600000);
  });

  const water = selectedWaterRows.map(function(item) {
    const time = new Date(
      item.date.getFullYear(),
      item.date.getMonth(),
      item.date.getDate(),
      item.hour || 0,
      0,
      0
    );

    return {
      time: time.toISOString(),
      parameter: item.parameter,
      value: item.value,
      unit: item.unit || 'm'
    };
  });

  // ----------------------------------------------------------
  // F. Tách từng chuỗi mưa, KHÔNG cộng các trạm vào nhau.
  // ----------------------------------------------------------
  const rainfallNames = {};
  rainfallRows.forEach(function(item) {
    rainfallNames[item.parameter] = true;
  });

  let selectedRainNames = Object.keys(rainfallNames);

  if (requestedRainfall.length) {
    selectedRainNames = selectedRainNames.filter(function(name) {
      return requestedRainfall.some(function(requested) {
        return normalizeText_(requested) === normalizeText_(name);
      });
    });
  }

  selectedRainNames.sort(function(a, b) {
    return a.localeCompare(b, 'vi');
  });

  const rainfall = [];
  const rainfallTotalsByParameter = {};

  selectedRainNames.forEach(function(name) {
    const seriesRows = rainfallRows
      .filter(function(item) {
        return normalizeText_(item.parameter) === normalizeText_(name);
      })
      .sort(function(a, b) {
        return a.date.getTime() + a.hour * 3600000 -
               (b.date.getTime() + b.hour * 3600000);
      });

    const seriesData = seriesRows.map(function(item) {
      const time = new Date(
        item.date.getFullYear(),
        item.date.getMonth(),
        item.date.getDate(),
        item.hour || 0,
        0,
        0
      );

      return {
        time: time.toISOString(),
        parameter: item.parameter,
        value: item.value,
        unit: item.unit || 'mm'
      };
    });

    const total = seriesRows.reduce(function(sum, item) {
      return sum + item.value;
    }, 0);

    rainfallTotalsByParameter[name] = total;

    rainfall.push({
      parameter: name,
      unit: seriesRows.length ? (seriesRows[0].unit || 'mm') : 'mm',
      data: seriesData,
      total: total
    });
  });

  // CHỦ Ý:
  // Nếu có nhiều chuỗi mưa, không cộng chúng thành một con số
  // "tổng lượng mưa" vì có thể là các trạm khác nhau.
  let totalRainfall = null;
  if (rainfall.length === 1) {
    totalRainfall = rainfall[0].total;
  }

  // ----------------------------------------------------------
  // G. Cập nhật cuối cùng.
  // ----------------------------------------------------------
  let updatedAt = null;
  if (water.length) {
    updatedAt = water[water.length - 1].time;
  } else if (rainfall.length) {
    const allRainTimes = rainfall.reduce(function(acc, series) {
      return acc.concat(series.data.map(function(p) { return p.time; }));
    }, []);

    if (allRainTimes.length) {
      updatedAt = allRainTimes.sort().pop();
    }
  }

  return {
    facility: facility,
    from: fromDate.toISOString().substring(0, 10),
    to: toDate.toISOString().substring(0, 10),
    water: water,
    rainfall: rainfall,
    rainfallPoints: rainfall.reduce(function(acc, series) {
      return acc.concat(series.data);
    }, []),
    totalRainfall: totalRainfall,
    rainfallTotalsByParameter: rainfallTotalsByParameter,
    limits: {
      mndbt: mndbt,
      mndgc: mndgc
    },
    selectedWaterParameter: selectedWaterParameter,
    count: water.length + rainfall.reduce(function(sum, s) { return sum + s.data.length; }, 0),
    updatedAt: updatedAt,
    source: 'AI_DATA'
  };
}

// ============================================================
// 12. LEGACY QUERY API
// ============================================================
// Giữ một API đọc đơn giản tương thích với doGet cũ:
// ?cong_trinh=...&thong_so=...&ngay=...&gio=...
// ============================================================

function queryLegacyAIData_(params) {
  const facility = clean_(params.cong_trinh || params.congTrinh);
  const parameter = clean_(params.thong_so || params.thongSo);
  const day = clean_(params.ngay);
  const hour = clean_(params.gio);

  if (!facility) {
    return {
      success: false,
      error: 'Thiếu tên công trình',
      query: {
        congTrinh: facility,
        thongSo: parameter,
        ngay: day,
        gio: hour
      },
      engine: 'AI_DATA'
    };
  }

  const data = getAIData_();
  const map = buildColumnMap_(data[0]);
  requireColumns_(map);

  const keyFacility = normalizeText_(facility);
  const keyParameter = normalizeText_(parameter);

  const results = [];

  for (let r = 1; r < data.length; r++) {
    const row = data[r];
    const rowFacility = normalizeText_(row[map['Công trình']]);
    const matchedFacility =
      rowFacility === keyFacility ||
      rowFacility.indexOf(keyFacility + ' (') === 0 ||
      rowFacility.indexOf(keyFacility + ' -') === 0;

    if (!matchedFacility) continue;

    if (day && clean_(row[map['Ngày']]) !== day) continue;
    if (hour && clean_(row[map['Giờ']]) !== hour) continue;

    const rowParameter = clean_(row[map['Thông số']]);

    if (parameter) {
      const rowKey = normalizeText_(rowParameter);
      const strippedRowKey = stripUnit_(rowParameter);
      const matchedParameter =
        rowKey === keyParameter ||
        strippedRowKey === stripUnit_(parameter);

      if (!matchedParameter) continue;
    }

    results.push({
      thang: map['Tháng'] !== undefined ? row[map['Tháng']] : '',
      ngay: row[map['Ngày']],
      gio: row[map['Giờ']],
      don_vi: map['Đơn vị'] !== undefined ? row[map['Đơn vị']] : '',
      cong_trinh: row[map['Công trình']],
      hang_muc: map['Hạng mục'] !== undefined ? row[map['Hạng mục']] : '',
      thong_so: rowParameter,
      don_vi_do: row[map['Đơn vị đo']],
      gia_tri: row[map['Giá trị']],
      nguon_du_lieu: map['Nguồn dữ liệu'] !== undefined ? row[map['Nguồn dữ liệu']] : 'AI_DATA'
    });
  }

  return {
    success: true,
    count: results.length,
    data: results,
    query: {
      congTrinh: facility,
      thongSo: parameter,
      ngay: day,
      gio: hour
    },
    engine: 'AI_DATA',
    source: 'AI_DATA',
    timestamp: new Date().toISOString()
  };
}

// ============================================================
// 13. MAIN doGet
// ============================================================

function doGet(e) {
  const started = new Date();
  let params = {};
  let api = '';

  try {
    params = getRequestParams_(e);
    api = clean_(params.api).toLowerCase();

    // --------------------------------------------------------
    // HEALTH
    // --------------------------------------------------------
    if (api === 'health' || api === 'ping') {
      return jsonResponse_(successResponse_(api, {
        status: 'ok',
        service: 'THUY LOI AI - Technical API',
        sheet: TECHNICAL_CONFIG.AI_SHEET_NAME
      }));
    }

    // --------------------------------------------------------
    // FACILITIES
    // --------------------------------------------------------
    if (api === 'facilities') {
      const facilities = getFacilities_();
      return jsonResponse_(successResponse_('facilities', facilities, {
        count: facilities.length
      }));
    }

    // --------------------------------------------------------
    // PARAMETERS
    // --------------------------------------------------------
    if (api === 'parameters') {
      const facility = clean_(params.facility || params.cong_trinh || params.congTrinh);
      const result = getFacilityParameters_(facility);

      return jsonResponse_(successResponse_('parameters', result, {
        facility: facility
      }));
    }

    // --------------------------------------------------------
    // CHART
    // --------------------------------------------------------
    if (api === 'chart') {
      const result = getChartData_(params);

      return jsonResponse_(successResponse_('chart', result, {
        facility: result.facility,
        count: result.count,
        elapsedMs: new Date().getTime() - started.getTime()
      }));
    }

    // --------------------------------------------------------
    // LEGACY / CHAT API
    // --------------------------------------------------------
    // Không có api nhưng có cong_trinh/congTrinh:
    // giữ khả năng truy vấn cũ.
    if (
      params.cong_trinh ||
      params.congTrinh
    ) {
      return jsonResponse_(queryLegacyAIData_(params));
    }

    // --------------------------------------------------------
    // API KHÔNG HỢP LỆ
    // --------------------------------------------------------
    return jsonResponse_(errorResponse_(api, 'API không hợp lệ', {
      supported: [
        'health',
        'facilities',
        'parameters',
        'chart',
        'legacy: cong_trinh/thong_so/ngay/gio'
      ]
    }));

  } catch (error) {
    console.error(error);

    return jsonResponse_(errorResponse_(api, error.message || error, {
      elapsedMs: new Date().getTime() - started.getTime()
    }));
  }
}

// ============================================================
// 14. TEST FUNCTIONS
// ============================================================
// Chạy các hàm này trong Apps Script trước khi deploy.
// ============================================================

function testTechnicalFacilities() {
  const result = getFacilities_();
  Logger.log(JSON.stringify(result, null, 2));
  return result;
}

function testTechnicalParameters() {
  const result = getFacilityParameters_('Hồ Đồng Tiến (H14)');
  Logger.log(JSON.stringify(result, null, 2));
  return result;
}

function testTechnicalChart() {
  const result = getChartData_({
    facility: 'Hồ Đồng Tiến (H14)',
    year: 2026,
    days: 7,
    waterParameter: '',
    rainfallParameters: ''
  });

  Logger.log(JSON.stringify(result, null, 2));
  return result;
}

function testTechnicalHealth() {
  const result = {
    ok: true,
    version: TECHNICAL_CONFIG.VERSION,
    sheet: TECHNICAL_CONFIG.AI_SHEET_NAME,
    timestamp: new Date().toISOString()
  };

  Logger.log(JSON.stringify(result, null, 2));
  return result;
}

// ============================================================
// HẾT CODE
// ============================================================
