export interface StreamService { name: string, rules: string[] }

const TIKTOK: StreamService = {
  name: 'TikTok',
  rules: [
    'DOMAIN-SUFFIX,tiktok.com',
    'DOMAIN-SUFFIX,sukka-tiktok-gap.example',
    // 'DOMAIN-SUFFIX,commented-out.example',
    'DOMAIN-KEYWORD,-tiktokcdn-com',
    'USER-AGENT,TikTok*'
  ]
};

const YOUTUBE: StreamService = {
  name: 'YouTube',
  rules: [
    'DOMAIN,youtube.com',
    'DOMAIN-SUFFIX,googlevideo.com',
    'DOMAIN-SUFFIX,sukka-youtube-gap.example',
    'USER-AGENT,YouTube*'
  ]
};
