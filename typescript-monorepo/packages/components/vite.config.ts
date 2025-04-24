import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import dts from 'vite-plugin-dts'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), dts({
    tsconfigPath: './tsconfig.app.json',
    include: ['src'],
  })],
  build: {
    lib: {
      entry: 'src/index.ts',
      formats: ['es'],
      fileName: "index"
    },
    rollupOptions: {
      external: [
        'react/jsx-runtime',
        '@repo/models'
      ],
    }
  }
})
