process.env.TZ = 'Asia/Shanghai';
import { createServer } from 'vite';
import fs from 'node:fs';
const server = await createServer({ root: process.cwd(), server: { middlewareMode: true }, appType: 'custom', logLevel: 'error' });
const roles = await server.ssrLoadModule('/src/mock/roles.ts');
const admin = await server.ssrLoadModule('/src/mock/admin.ts');
const out = admin.ADMIN_USERS.map((u) => { const e = roles.effectiveAccess(u); return { id: u.id, rs: e.rs.map((r) => r.id), func: e.func, objs: e.objs, areasIn: e.areasIn }; });
fs.writeFileSync(process.argv[2], JSON.stringify(out, null, 1));
await server.close();
