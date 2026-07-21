// Synthetic parser fixture; no upstream rules copied.
export const GLOBAL = {
  GOOGLE: {
    ruleset: false,
    domains: [
      'reference-main.example',
      'reference-ai.example',
      'reference-video.example',
      '$reference-exact.example',
      'reference-only.example',
    ]
  },
  CLOUDFLARE: {
    domains: ['unrelated.example']
  }
};
