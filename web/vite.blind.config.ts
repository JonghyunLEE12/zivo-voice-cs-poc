import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
export default defineConfig({
  root: 'src/blind',
  base: '/blind/',
  build: {
    outDir: '../../dist-blind',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('./src/blind/index.html', import.meta.url)),
        results: fileURLToPath(new URL('./src/blind/results.html', import.meta.url)),
      },
    },
  },
});
