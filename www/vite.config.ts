import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  // Relative asset URLs: the build is served both at runsascoded.github.io/git-didi/ and by `git-didi serve` at /
  base: './',
  plugins: [react()],
  server: {
    port: 6092,
    host: true,
    allowedHosts: true,  // trusted-tailnet dev server, reached by bare MagicDNS name
    // `git-didi serve`'s API (run it alongside `pnpm dev`)
    proxy: { '/api': 'http://127.0.0.1:6093' },
  },
})
