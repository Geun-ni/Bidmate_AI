import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버의 /api 요청을 백엔드로 넘긴다 (배포에서는 nginx가 같은 일을 한다).
// 기본값은 팀 배포 서버. 로컬 백엔드(python -m backend.server)를 쓰려면
//   frontend-react/.env.local 에 BIDMATE_API=http://127.0.0.1:8000
const DEFAULT_API = "https://211-233-216-208.sslip.io";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.BIDMATE_API || DEFAULT_API;
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target,
          changeOrigin: true,
          secure: true,
          // 백엔드 세션 쿠키를 개발 주소(localhost)에서도 쓰도록 쿠키 도메인을 지운다
          cookieDomainRewrite: "",
        },
      },
    },
  };
});
