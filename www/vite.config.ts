import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig(({ command }) => ({
  // GH Pages serves the build at runsascoded.github.io/git-didi/
  base: command === 'build' ? '/git-didi/' : '/',
  plugins: [react()],
  server: {
    port: 6092,
    host: true,
    allowedHosts: true,  // trusted-tailnet dev server, reached by bare MagicDNS name
  },
}))
