"use strict";
const readline = require("node:readline");
const { chromium } = require(process.argv[2]);
const proxy = process.argv[3];
let browser, context, page, frame, lastActivity=Date.now();
let logs=[];
const httpURL = (url) => /^(https?):\/\//i.test(url);
async function start() {
  browser = await chromium.launch({
    headless: true, chromiumSandbox: true, executablePath: process.argv[4],
    proxy: {server: proxy, bypass: "<-loopback>"},
    args: [
      "--disable-quic", "--disable-extensions", "--disable-background-networking",
      "--disable-component-update", "--disable-sync", "--no-first-run",
      "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
      "--disable-features=WebTransport,DirectSockets,WebBluetooth,WebUSB",
      "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"
    ]
  });
  context=await browser.newContext({acceptDownloads:false,serviceWorkers:"block",ignoreHTTPSErrors:false});
  await context.route("**/*", async route => {
    // The proxy validates HTTP(S) and upgrade requests at connect time. This also fences file/ftp.
    if (!httpURL(route.request().url())) return route.abort("blockedbyclient");
    return route.continue();
  });
  const cdp=await browser.newBrowserCDPSession();
  const contexts=await cdp.send("Target.getBrowserContexts");
  for(const id of contexts.browserContextIds)
    await cdp.send("Browser.setDownloadBehavior",{behavior:"deny",browserContextId:id});
  context.on("page", attach);
  page=await context.newPage(); frame=page.mainFrame();
}
function attach(p) {
  p.on("console", msg => {logs.push(msg.type()+": "+msg.text()); if(logs.length>100) logs.shift();});
  p.on("pageerror", error => {logs.push(String(error)); if(logs.length>100) logs.shift();});
  p.on("framenavigated", f => {
    const u=f.url();
    if(u !== "about:blank" && !httpURL(u)) p.close().catch(()=>{});
  });
}
function locator(target) {
  const match=/^@?e(\d+)$/.exec(target);
  return frame.locator(match ? '[data-komatso-public-ref="e'+match[1]+'"]' : target).first();
}
async function snapshot(limit=18000) {
  return frame.evaluate(limit => {
    const nodes=[...document.querySelectorAll("a,button,input,textarea,select,[role=button],[role=link],summary")];
    const elements=[];
    for(const [i,node] of nodes.entries()) {
      const ref="e"+(i+1); node.dataset.komatsoPublicRef=ref;
      if(node.getClientRects().length) elements.push({ref:"@"+ref,tag:node.tagName.toLowerCase(),
        text:(node.innerText||node.getAttribute("aria-label")||node.getAttribute("placeholder")||node.getAttribute("name")||"").slice(0,240),
        href:node.getAttribute("href")||undefined,type:node.getAttribute("type")||undefined});
    }
    return {title:document.title,url:location.href,text:(document.body?.innerText||"").slice(0,limit),elements,
      frames:[...document.querySelectorAll("iframe")].map((f,i)=>({index:i+1,src:f.src,name:f.name}))};
  }, Math.max(1000,Math.min(60000,Number(limit)||18000)));
}
async function execute(action,args) {
  lastActivity=Date.now();
  if(action==="close") {await browser.close(); return {success:true,closed:true};}
  if(action==="clear") {await page.goto("about:blank"); frame=page.mainFrame(); return {success:true};}
  if(action==="navigate") {
    if(!httpURL(args.url)) throw Error("Only public HTTP/HTTPS URLs allowed");
    await page.goto(args.url,{waitUntil:"domcontentloaded",timeout:40000});
    frame=page.mainFrame();
    await page.waitForTimeout(300);
    return {success:true,...await snapshot()};
  }
  if(!page || page.isClosed()) throw Error("Navigate to a public page first");
  if(action==="snapshot") return {success:true,...await snapshot(args.char_limit)};
  if(action==="click") await locator(args.target).click({timeout:10000});
  else if(action==="type") await locator(args.target).fill(String(args.text||""),{timeout:10000});
  else if(action==="hover") await locator(args.target).hover({timeout:10000});
  else if(action==="select") await locator(args.target).selectOption(args.value,{timeout:10000});
  else if(action==="press") await page.keyboard.press(args.key);
  else if(action==="scroll") await page.mouse.wheel(Number(args.x)||0,Number(args.y)||600);
  else if(action==="back") await page.goBack({waitUntil:"domcontentloaded",timeout:30000});
  else if(action==="tabs") return {success:true,tabs:context.pages().map((p,i)=>({index:i,url:p.url()}))};
  else if(action==="switch_tab") {
    const chosen=context.pages()[Number(args.index)];
    if(!chosen) throw Error("Tab not found");
    page=chosen; frame=page.mainFrame();
  }
  else if(action==="frame") {
    frame=Number(args.index)===0 ? page.mainFrame() : page.frames()[Number(args.index)];
    if(!frame) throw Error("Frame not found");
  }
  else if(action==="drag") await locator(args.source).dragTo(locator(args.target),{timeout:10000});
  else if(action==="screenshot") return {success:true,url:page.url(),png:(await page.screenshot({fullPage:false,timeout:15000})).toString("base64")};
  else if(action==="images") return {success:true,images:await frame.evaluate(()=>[...document.images].map(img=>({url:img.currentSrc||img.src,alt:img.alt,width:img.naturalWidth,height:img.naturalHeight})))};
  else if(action==="console") return args.expression
    ? {success:true,result:await frame.evaluate(args.expression)} : {success:true,logs};
  else throw Error("Unknown public browser action");
  return {success:true,...await snapshot()};
}
let chain=Promise.resolve();
readline.createInterface({input:process.stdin,terminal:false}).on("line", line => {
  chain=chain.then(async()=>{
    let req;
    try {req=JSON.parse(line); const result=await execute(req.action,req.args||{}); process.stdout.write(JSON.stringify({id:req.id,url:page?.url(),...result})+"\n");}
    catch(error) {process.stdout.write(JSON.stringify({id:req?.id,success:false,error:String(error.message).slice(0,1000)})+"\n");}
  });
});
process.stdin.on("end",()=>{browser?.close().finally(()=>process.exit(0));});
setInterval(()=>{if(Date.now()-lastActivity>120000) browser?.close().finally(()=>process.exit(0));},10000).unref();
start().then(()=>process.stdout.write('{"ready":true}\n')).catch(error=>{
  process.stdout.write(JSON.stringify({ready:false,error:String(error.message).slice(0,1000)})+"\n"); process.exit(1);
});
