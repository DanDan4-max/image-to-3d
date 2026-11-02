// The Cloudflare side of Image to 3D (a free Worker, put up by app/cloud.py).
// - Holds the models (models.json, m/<name>/model.glb ...) for the Chromebook file, which runs from file:// and so
//   needs CORS headers on everything.
// - Remembers where the PC is right now (its quick-tunnel address changes every time the app starts), so the
//   Chromebook file can find it. Only the PC can change that: it sends PC_SECRET. Using the PC itself needs the
//   passcode, which the PC checks.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-Image-To-3D, X-Key',
  'Access-Control-Max-Age': '86400',
};
const TUNNEL = /^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/;

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { ...CORS, 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });

async function sameSecret(given, secret) {
  if (!secret) return false;
  const [a, b] = await Promise.all([given, secret].map((s) => crypto.subtle.digest('SHA-256', new TextEncoder().encode(s))));
  return crypto.subtle.timingSafeEqual(a, b);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });

    if (url.pathname === '/pc' && req.method === 'POST') {
      // the PC telling us its address (or null when it closes)
      if (!(await sameSecret(req.headers.get('Authorization') || '', 'Bearer ' + env.PC_SECRET))) return json({ error: 'not the PC' }, 403);
      const body = await req.json().catch(() => ({}));
      if (body.url && !TUNNEL.test(body.url)) return json({ error: 'bad address' }, 400);
      if (body.url) await env.PC.put('url', body.url);
      else await env.PC.delete('url');
      return json({ ok: true });
    }
    if (url.pathname === '/pc') {
      // where's the PC? Only answer with it if it really answers right now.
      const pc = await env.PC.get('url');
      let up = false;
      if (pc) {
        try {
          up = (await fetch(pc + '/ping', { signal: AbortSignal.timeout(5000) })).ok;
        } catch {}
      }
      return json({ url: up ? pc : null });
    }

    const res = await env.ASSETS.fetch(req);
    const out = new Response(res.body, res);
    for (const [k, v] of Object.entries(CORS)) out.headers.set(k, v);
    if (url.pathname === '/models.json') out.headers.set('Cache-Control', 'no-cache');
    return out;
  },
};
