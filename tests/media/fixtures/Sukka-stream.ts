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

const BILIBILI_INTL: StreamService = {
  name: 'Bilibili International',
  rules: [
    'DOMAIN-SUFFIX,biliintl.com',
    'DOMAIN,apm-misaka.biliapi.net',
    'DOMAIN,upos-bstar-mirrorakam.akamaized.net',
    'DOMAIN,upos-bstar1-mirrorakam.akamaized.net',
    'DOMAIN-SUFFIX,bilibili.tv',
    'PROCESS-NAME,com.bstar.intl'
  ]
};
