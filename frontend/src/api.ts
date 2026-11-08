export async function api<T>(path:string, init?:RequestInit):Promise<T>{
  const res=await fetch('/api'+path,init)
  if(!res.ok){
    let message=`请求失败 (${res.status})`
    try{const body=await res.json();message=Array.isArray(body.detail)?body.detail.map((x:{msg:string})=>x.msg).join('；'):body.detail||message}catch{}
    throw new Error(message)
  }
  return res.json()
}
export const post=<T,>(path:string,body?:unknown)=>api<T>(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body??{})})
export const patch=<T,>(path:string,body:unknown)=>api<T>(path,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
