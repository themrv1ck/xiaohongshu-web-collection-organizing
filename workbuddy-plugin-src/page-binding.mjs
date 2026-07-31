const USER_ID_RE = /^[0-9a-f]{24}$/;


function parsedXhsPage(pageUrl) {
  const parsed = new URL(pageUrl);
  const hostname = parsed.hostname.toLowerCase();
  if (
    parsed.protocol !== 'https:'
    || !(hostname === 'xiaohongshu.com' || hostname.endsWith('.xiaohongshu.com'))
  ) {
    throw new Error('page_url_invalid');
  }
  return parsed;
}


function sourceForTab(tab) {
  if (tab === 'fav') return 'collection';
  if (['liked', 'like'].includes(tab)) return 'liked';
  throw new Error('page_source_tab_mismatch');
}


export function canonicalPageBinding(pageUrl, expectedSource = undefined) {
  const parsed = parsedXhsPage(pageUrl);
  const tab = String(parsed.searchParams.get('tab') || '').trim().toLowerCase();
  const source = sourceForTab(tab);
  if (expectedSource !== undefined && source !== expectedSource) {
    throw new Error('page_source_tab_mismatch');
  }
  return `${parsed.origin}${parsed.pathname}?tab=${tab}`;
}


export function receiptBindingsForPage(userId, pageUrl) {
  const checkedUserId = String(userId || '').trim().toLowerCase();
  if (!USER_ID_RE.test(checkedUserId)) throw new Error('page_user_id_invalid');
  const parsed = parsedXhsPage(pageUrl);
  const match = parsed.pathname.match(/^\/user\/profile\/([0-9a-fA-F]{24})\/?$/);
  if (!match || match[1].toLowerCase() !== checkedUserId) {
    throw new Error('page_user_id_mismatch');
  }
  const tab = String(parsed.searchParams.get('tab') || '').trim().toLowerCase();
  const source = sourceForTab(tab);
  return {
    user_id: checkedUserId,
    page_binding: canonicalPageBinding(pageUrl, source),
    source,
  };
}
