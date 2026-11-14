// POST /api/action  网页提交"通过 / 打回"（要 REVIEW_TOKEN）
// GET  /api/action  Mac 拉走待处理动作（要 SYNC_TOKEN），拉走即删
//
// 用 KV 的 list 存动作：key = "action:<时间戳>:<随机>"，value = {taskId, action}。
// Mac 拉到后自己删，避免重复执行。

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  })
}

export async function onRequestPost({ env, request }) {
  const review = request.headers.get('X-Review-Token')
  if (!env.REVIEW_TOKEN || review !== env.REVIEW_TOKEN) {
    return json({ ok: false, error: 'unauthorized' }, 401)
  }
  let body
  try {
    body = await request.json()
  } catch {
    return json({ ok: false, error: 'bad json' }, 400)
  }
  const { taskId, action } = body || {}
  if (!taskId || !['approve', 'reject'].includes(action)) {
    return json({ ok: false, error: 'bad payload' }, 400)
  }
  const key = `action:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`
  await env.COCKPIT_KV.put(key, JSON.stringify({ taskId, action, at: Date.now() }))
  return json({ ok: true })
}

export async function onRequestGet({ env, request }) {
  const sync = request.headers.get('X-Sync-Token')
  if (!env.SYNC_TOKEN || sync !== env.SYNC_TOKEN) {
    return json({ ok: false, error: 'unauthorized' }, 401)
  }
  const list = await env.COCKPIT_KV.list({ prefix: 'action:' })
  const actions = []
  for (const k of list.keys) {
    const raw = await env.COCKPIT_KV.get(k.name)
    if (raw) actions.push({ key: k.name, ...JSON.parse(raw) })
  }
  // 拉走就删，Mac 只处理一次
  for (const k of list.keys) await env.COCKPIT_KV.delete(k.name)
  return json({ ok: true, actions })
}
