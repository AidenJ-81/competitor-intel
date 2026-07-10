// Xi C&A 경쟁사 인텔리전스 대시보드 — 정적 서빙 + Anthropic API 프록시
// API 키는 서버 환경변수(ANTHROPIC_API_KEY)에만 존재하며 브라우저로 노출되지 않는다.
const express = require("express");
const path = require("path");
const app = express();
const PORT = process.env.PORT || 3000;
const API_KEY = process.env.ANTHROPIC_API_KEY;
const ANTHROPIC_VERSION = process.env.ANTHROPIC_VERSION || "2023-06-01";
const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
app.use(express.json({ limit: "5mb" }));
app.use(express.static(path.join(__dirname)));
// 헬스체크 (Coolify 등에서 사용)
app.get("/health", (_req, res) => {
  res.json({ ok: true, keyConfigured: Boolean(API_KEY) });
});
// Anthropic Messages API 프록시
app.post("/api/messages", async (req, res) => {
  if (!API_KEY) {
    return res.status(500).json({
      error: { message: "서버에 ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다." },
    });
  }
  try {
    const upstream = await fetch(ANTHROPIC_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
      },
      body: JSON.stringify(req.body),
    });
    const data = await upstream.json();
    res.status(upstream.status).json(data);
  } catch (e) {
    res.status(502).json({ error: { message: `프록시 오류: ${e.message}` } });
  }
});
// SPA 폴백 — 그 외 GET 요청은 index.html 반환
app.get("*", (_req, res) => {
  res.sendFile(path.join(__dirname, "index.html"));
});
app.listen(PORT, "0.0.0.0", () => {
  console.log(`Xi C&A dashboard running on port ${PORT}`);
  if (!API_KEY) {
    console.warn("⚠  ANTHROPIC_API_KEY 미설정 — AI 기능(뉴스/DART/파이프라인/채팅)이 작동하지 않습니다.");
  }
});
