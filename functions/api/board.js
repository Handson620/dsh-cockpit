// GET  /api/board  网页读看板（要 REVIEW_TOKEN）
// POST /api/board  Mac 推送看板快照（要 SYNC_TOKEN）
//
// 数据存在 KV 的 "board" 键里。Mac 定时写，网页打开时读。
// 这样不用 git push 数据，实时性最好，代码变更才需要 push。

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  })
}

export async function onRequestGet({ env, request }) {
  const review = request.headers.get('X-Review-Token')
  const sync = request.headers.get('X-Sync-Token')
  const okReview = env.REVIEW_TOKEN && review === env.REVIEW_TOKEN
  const okSync = env.SYNC_TOKEN && sync === env.SYNC_TOKEN
  if (!okReview && !okSync) return json({ ok: false, error: 'unauthorized' }, 401)

  const raw = await env.COCKPIT_KV.get('board')
  return json({ ok: true, board: raw ? JSON.parse(raw) : null })
}

export async function onRequestPost({ env, request }) {
  const sync = request.headers.get('X-Sync-Token')
  if (!env.SYNC_TOKEN || sync !== env.SYNC_TOKEN) {
    return json({ ok: false, error: 'unauthorized' }, 401)
  }
  let body
  try {
    body = await request.json()
  } catch {
    return json({ ok: false, error: 'bad json' }, 400)
  }
  if (!body || !Array.isArray(body.tasks)) {
    return json({ ok: false, error: 'bad payload' }, 400)
  }
  await env.COCKPIT_KV.put('board', JSON.stringify(body))
  return json({ ok: true, count: body.tasks.length })
}
