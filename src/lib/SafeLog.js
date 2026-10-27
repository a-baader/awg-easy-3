'use strict';

// Исключения инструментов могут включать строку неверной конфигурации.
const safeMessage = error => String(error?.message ?? error)
  .replace(/vpn:\/\/[^\s"']+/gi, '[ПРОФИЛЬ СКРЫТ]')
  .replace(/[A-Za-z0-9+/]{43}=/g, '[КЛЮЧ СКРЫТ]')
  .replace(/\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}/g, '[ХЕШ СКРЫТ]')
  .replace(/(awg_easy_3_session=)[^;\s]+/g, '$1[СЕССИЯ СКРЫТА]')
  .replace(/((?:privateKey|presharedKey|sessionSecret|passwordHash|password)\s*["']?\s*[:=]\s*)["']?[^\s,"'}]+/gi, '$1[СЕКРЕТ СКРЫТ]');
module.exports = { safeMessage };
