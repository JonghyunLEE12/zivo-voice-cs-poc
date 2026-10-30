import { defineConfig } from 'vite';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { readFileSync, readdirSync } from 'node:fs';
const require = createRequire(import.meta.url);
const vadDir = dirname(require.resolve('@ricky0123/vad-web'));
const ortDir = dirname(createRequire(require.resolve('@ricky0123/vad-web')).resolve('onnxruntime-web/wasm'));
const assets = new Map([
  ...['vad.worklet.bundle.min.js', 'silero_vad_v5.onnx'].map(name => [name, resolve(vadDir, name)] as const),
  ...readdirSync(ortDir).filter(name => /^ort-wasm-simd-threaded\.(wasm|mjs)$/.test(name)).map(name => [name, resolve(ortDir, name)] as const),
]);
export default defineConfig({
  plugins: [{
    name: 'local-vad-assets',
    generateBundle() {
      for (const [name, path] of assets) this.emitFile({ type: 'asset', fileName: `vad/${name}`, source: readFileSync(path) });
    },
    configureServer(server) {
      server.middlewares.use('/vad', (req, res, next) => {
        const name = req.url?.slice(1).split('?')[0] || '';
        const path = assets.get(name);
        if (!path) return next();
        res.setHeader('Content-Type', name.endsWith('.wasm') ? 'application/wasm' : name.endsWith('.onnx') ? 'application/octet-stream' : 'text/javascript');
        res.end(readFileSync(path));
      });
    },
  }],
  server: { proxy: { '/ws': { target: 'ws://127.0.0.1:8000', ws: true } } },
});
