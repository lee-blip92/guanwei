export const platformNames:Record<string,string>={xhs:'小红书',dy:'抖音',ks:'快手',bili:'B站',wb:'微博',tieba:'贴吧',zhihu:'知乎'}
export const platformName=(id:string)=>platformNames[id]||id
export const platformList=(ids?:string[])=> (ids||['xhs']).map(platformName).join('、')
export const sortNames:Record<string,string>={general:'综合 / 平台默认',time_descending:'最新',popularity_descending:'热门（各平台口径不同）'}
