import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../staticfiles/staff",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        kds: resolve(__dirname, "src/kds/main.tsx"),
        reception: resolve(__dirname, "src/reception/main.tsx"),
      },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name]-[hash].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
});
