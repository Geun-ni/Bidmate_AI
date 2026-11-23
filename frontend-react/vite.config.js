import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 기존 Python 데모 서버(백엔드 수정 없음)로 API 요청을 넘긴다.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
