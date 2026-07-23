// ============================================================
//  Vivid Windows — Summer Bonus Tracker Report
//  Google Apps Script
//
//  SETUP:
//    1. Open a blank Google Sheet
//    2. Extensions → Apps Script
//    3. Paste this entire file, click Save
//    4. Run → runReport  (or use the "Vivid Reports" menu)
//    5. Authorize when prompted (needed to call external URL)
// ============================================================

var API_BASE    = 'https://terros-dashboard.onrender.com';
var BONUS_START = '2026-07-01';   // competition start date

// Office sort order
var OFFICE_ORDER = ['Southern Utah', 'Northern Utah', 'Eastern Idaho', 'Northern California'];

// Brand colors
var COLOR_HEADER  = '#185c4d';
var COLOR_4PCT    = '#f5c542';
var COLOR_4PCT_BG = '#fffbec';
var COLOR_2PCT    = '#00c6a2';
var COLOR_2PCT_BG = '#f0faf7';
var COLOR_SG_BG   = '#fff8e1';
var COLOR_SG_TXT  = '#b45309';
var COLOR_ODD     = '#f8f8f8';
var COLOR_WHITE   = '#ffffff';


// ── Add menu when sheet opens ────────────────────────────────
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Vivid Reports')
    .addItem('Refresh now', 'runReport')
    .addSeparator()
    .addItem('Auto-refresh every hour', 'setupHourlyTrigger')
    .addItem('Auto-refresh on open', 'setupOnOpenTrigger')
    .addItem('Turn off auto-refresh', 'removeTriggers')
    .addToUi();
}


// ── Trigger setup ─────────────────────────────────────────────

// Run this ONCE to refresh the report every hour automatically
function setupHourlyTrigger() {
  removeTriggers();   // clear any existing triggers first
  ScriptApp.newTrigger('runReport')
    .timeBased()
    .everyHours(1)
    .create();
  SpreadsheetApp.getUi().alert('Auto-refresh enabled — report will update every hour.');
}

// Run this ONCE to refresh whenever the sheet is opened
function setupOnOpenTrigger() {
  removeTriggers();
  ScriptApp.newTrigger('runReport')
    .forSpreadsheet(SpreadsheetApp.getActiveSpreadsheet())
    .onOpen()
    .create();
  SpreadsheetApp.getUi().alert('Auto-refresh enabled — report will update every time you open this sheet.');
}

// Remove all triggers (turns off auto-refresh)
function removeTriggers() {
  ScriptApp.getProjectTriggers().forEach(function(t) {
    ScriptApp.deleteTrigger(t);
  });
}


// ── Main entry point ─────────────────────────────────────────
function runReport() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  // Date range: bonus start → today
  var today   = new Date();
  var todayStr = fmtDate(today);

  // Fetch deals from Render API
  var url  = API_BASE + '/api/odoo/deals?start=' + BONUS_START + '&end=' + todayStr;
  var resp;
  try {
    resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  } catch (e) {
    SpreadsheetApp.getUi().alert('Could not reach the API:\n' + e.message);
    return;
  }

  if (resp.getResponseCode() !== 200) {
    SpreadsheetApp.getUi().alert('API returned ' + resp.getResponseCode() + ':\n' + resp.getContentText().slice(0, 300));
    return;
  }

  var data  = JSON.parse(resp.getContentText());
  var deals = data.deals || [];
  var stale = data.stale === true;

  // Normalize: for self-gen deals where setter field is blank, fall back to closer
  deals.forEach(function(d) {
    if (!(d.setter || '').trim() && (d.closer || '').trim()) {
      d.setter = d.closer;
    }
  });

  // Group: setter → weekKey → { week, deals[] }
  var setterMap = buildSetterMap(deals);

  // Write sheets
  buildDealsSheet(ss, setterMap);
  buildSummarySheet(ss, setterMap);

  // Move "Qualifying Deals" to first position
  var qs = ss.getSheetByName('Qualifying Deals');
  ss.setActiveSheet(qs);
  ss.moveActiveSheet(1);

  var msg = 'Done! ' + deals.length + ' deals fetched (Jul 1 – ' + todayStr + ').';
  if (stale) msg += '\n\n⚠️ Note: data may be stale (API key expired). Refresh the Odoo key and re-run.';
  SpreadsheetApp.getUi().alert(msg);
}


// ── Group deals by setter → Sun-Sat week ─────────────────────
function buildSetterMap(deals) {
  var map = {};   // { setter: { office, weeks: { weekKey: { week, deals[] } } } }

  deals.forEach(function(d) {
    var setter = (d.setter || '').trim();
    if (!setter) return;
    var dealDate = d.date_deadline || d.date_closed || '';
    if (!dealDate || dealDate < BONUS_START) return;

    var week = getSunSatWeek(dealDate);
    if (!week) return;

    if (!map[setter]) map[setter] = { office: d.office || 'Unknown', weeks: {} };
    if (!map[setter].weeks[week.key]) map[setter].weeks[week.key] = { week: week, deals: [] };
    map[setter].weeks[week.key].deals.push(d);
  });

  return map;
}


// ── Sheet 1: Qualifying Deals ─────────────────────────────────
function buildDealsSheet(ss, setterMap) {
  var sheet = getOrCreateSheet(ss, 'Qualifying Deals');

  // Header row
  var headers = ['Office', 'Setter', 'Week', 'Deals that week', 'Bonus %', 'Opportunity Name', 'Self-Generated', 'Odoo Link'];
  writeHeader(sheet, headers, COLOR_HEADER);

  // Build rows
  var rows    = [];
  var rowMeta = [];   // { bonus, isSG, hasLink, link }

  sortedSetters(setterMap).forEach(function(setter) {
    var info = setterMap[setter];
    sortedWeekKeys(info.weeks).forEach(function(wk) {
      var w   = info.weeks[wk];
      var cnt = w.deals.length;
      if (cnt < 2) return;  // not a qualifying week
      var bonusPct = cnt >= 3 ? '4%' : '2%';

      w.deals.forEach(function(d) {
        var isSG   = !!(d.closer && d.closer.trim() === (d.setter || '').trim());
        var link   = d.id ? 'https://myvivid.odoo.com/odoo/crm/' + d.id : '';
        rows.push([
          info.office,
          setter,
          w.week.label,
          cnt,
          bonusPct,
          d.name || '',
          isSG ? 'Yes' : '',
          link ? 'Open in Odoo' : ''
        ]);
        rowMeta.push({ bonus: bonusPct, isSG: isSG, link: link });
      });
    });
  });

  if (rows.length === 0) {
    sheet.getRange(2, 1).setValue('No qualifying deals found yet.');
    return;
  }

  // Write data
  sheet.getRange(2, 1, rows.length, headers.length).setValues(rows);

  // Formatting per row
  rows.forEach(function(row, i) {
    var r       = i + 2;
    var meta    = rowMeta[i];
    var rowBg   = i % 2 === 0 ? COLOR_WHITE : COLOR_ODD;

    sheet.getRange(r, 1, 1, headers.length).setBackground(rowBg);

    // Bonus % cell
    var bonusCell = sheet.getRange(r, 5);
    if (meta.bonus === '4%') {
      bonusCell.setBackground(COLOR_4PCT).setFontColor('#1a1400').setFontWeight('bold');
    } else {
      bonusCell.setBackground(COLOR_2PCT).setFontColor('#ffffff').setFontWeight('bold');
    }
    bonusCell.setHorizontalAlignment('center');

    // SG cell
    if (meta.isSG) {
      sheet.getRange(r, 7).setBackground(COLOR_SG_BG).setFontColor(COLOR_SG_TXT).setFontWeight('bold').setHorizontalAlignment('center');
    }

    // Odoo hyperlink
    if (meta.link) {
      sheet.getRange(r, 8).setFormula('=HYPERLINK("' + meta.link + '","Open in Odoo")');
      sheet.getRange(r, 8).setFontColor('#0066cc');
    }
  });

  // Center numeric / short columns
  sheet.getRange(2, 3, rows.length, 3).setHorizontalAlignment('center');
  sheet.getRange(2, 7, rows.length, 1).setHorizontalAlignment('center');

  // Column widths
  sheet.setColumnWidth(1, 145);
  sheet.setColumnWidth(2, 150);
  sheet.setColumnWidth(3, 105);
  sheet.setColumnWidth(4, 115);
  sheet.setColumnWidth(5, 85);
  sheet.setColumnWidth(6, 300);
  sheet.setColumnWidth(7, 115);
  sheet.setColumnWidth(8, 120);

  sheet.setFrozenRows(1);
}


// ── Sheet 2: Setter Summary ───────────────────────────────────
function buildSummarySheet(ss, setterMap) {
  var sheet = getOrCreateSheet(ss, 'Setter Summary');

  var headers = ['Setter', 'Office', 'Total Deals (MTD)', 'Qualifying Deals', '2% Weeks', '4% Weeks', 'SG Deals', 'SG Qualifying'];
  writeHeader(sheet, headers, COLOR_HEADER);

  var rows    = [];
  var rowMeta = [];

  sortedSetters(setterMap).forEach(function(setter) {
    var info   = setterMap[setter];
    var total  = 0, qual = 0, wks2 = 0, wks4 = 0, sg = 0, sgQual = 0;

    sortedWeekKeys(info.weeks).forEach(function(wk) {
      var w   = info.weeks[wk];
      var cnt = w.deals.length;
      total  += cnt;
      var isQual = cnt >= 2;
      if (isQual) {
        qual += cnt;
        if (cnt >= 3) wks4++; else wks2++;
      }
      w.deals.forEach(function(d) {
        var isSG = !!(d.closer && d.closer.trim() === (d.setter || '').trim());
        if (isSG) { sg++; if (isQual) sgQual++; }
      });
    });

    rows.push([setter, info.office, total, qual, wks2, wks4, sg, sgQual]);
    rowMeta.push({ qual: qual, wks4: wks4 });
  });

  if (rows.length === 0) {
    sheet.getRange(2, 1).setValue('No data found.');
    return;
  }

  sheet.getRange(2, 1, rows.length, headers.length).setValues(rows);

  rows.forEach(function(_, i) {
    var r    = i + 2;
    var meta = rowMeta[i];
    var rowBg = i % 2 === 0 ? COLOR_WHITE : COLOR_ODD;
    sheet.getRange(r, 1, 1, headers.length).setBackground(rowBg);

    if (meta.qual > 0) {
      sheet.getRange(r, 4).setBackground(COLOR_2PCT_BG).setFontColor('#0f6e56').setFontWeight('bold');
    }
    if (meta.wks4 > 0) {
      sheet.getRange(r, 6).setBackground(COLOR_4PCT_BG).setFontColor('#854f0b').setFontWeight('bold');
    }
  });

  sheet.getRange(2, 3, rows.length, 6).setHorizontalAlignment('center');

  sheet.setColumnWidth(1, 150);
  sheet.setColumnWidth(2, 150);
  for (var c = 3; c <= 8; c++) sheet.setColumnWidth(c, 120);
  sheet.setFrozenRows(1);
}


// ── Helpers ───────────────────────────────────────────────────

function getSunSatWeek(dateStr) {
  if (!dateStr) return null;
  var p   = dateStr.split('-');
  var d   = new Date(parseInt(p[0]), parseInt(p[1]) - 1, parseInt(p[2]));
  var dow = d.getDay();
  var sun = new Date(d); sun.setDate(d.getDate() - dow);
  var sat = new Date(sun); sat.setDate(sun.getDate() + 6);
  var key = fmtDate(sun);
  // Clamp display start to BONUS_START
  var dispStart = key < BONUS_START ? BONUS_START : key;
  var ds = dispStart.split('-'), de = fmtDate(sat).split('-');
  var label = parseInt(ds[1]) + '/' + parseInt(ds[2]) + '–' + parseInt(de[1]) + '/' + parseInt(de[2]);
  return { key: key, start: key, end: fmtDate(sat), label: label };
}

function fmtDate(d) {
  var pad = function(n) { return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

function sortedSetters(setterMap) {
  return Object.keys(setterMap).sort(function(a, b) {
    var oa = setterMap[a].office, ob = setterMap[b].office;
    var ia = OFFICE_ORDER.indexOf(oa), ib = OFFICE_ORDER.indexOf(ob);
    if (ia < 0) ia = 99;
    if (ib < 0) ib = 99;
    if (ia !== ib) return ia - ib;
    return a.localeCompare(b);
  });
}

function sortedWeekKeys(weeks) {
  return Object.keys(weeks).sort();
}

function getOrCreateSheet(ss, name) {
  var sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
  } else {
    sheet.clearContents();
    sheet.clearFormats();
  }
  return sheet;
}

function writeHeader(sheet, headers, bgColor) {
  var range = sheet.getRange(1, 1, 1, headers.length);
  range.setValues([headers]);
  range.setBackground(bgColor);
  range.setFontColor('#ffffff');
  range.setFontWeight('bold');
  range.setFontSize(11);
  range.setVerticalAlignment('middle');
  sheet.setRowHeight(1, 32);
}
