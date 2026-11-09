"""AI preparation inside the existing Repairs form/lifecycle, never an AI Writer."""
import logging

from tools.bale_ui import Action, InlineKeyboardBuilder, StateStore
from .entry_bale import RepairsEntryHandler, ROOT, LABELS, normalized, permitted, worker
from .entry_draft_store import DraftStore, ACTORS
from .entry_extract import extract, intent

log = logging.getLogger(__name__)


class DailyAIHandler(RepairsEntryHandler):
    namespace = 'daily_ai'
    entry_command = 'ادامه پیش نویس'

    def __init__(self, *, drafts=None, extractor=extract, **kwargs):
        self.drafts = drafts or DraftStore()
        self.extractor = extractor
        kwargs.setdefault('authorize', lambda actor: str(actor) in ACTORS and permitted(actor))
        super().__init__(**kwargs)
        for key,session in self.sessions.items():
            session.update(stage='PAUSED', revision='', expires=self.clock()+1800)
            if session.get('draft'):
                d=self.drafts.load(session['draft'],*key)
                d['binding']=None
                self.drafts.update(d,'restart')
        self.persist()

    def current(self,key):
        return self.drafts.load(self.sessions[key]['draft'],*key)

    def actor_keyboard(self,stage,actor):
        s=self.sessions[(str(actor),str(actor))]
        def button(name,label):return (Action(name,label,'repairs.edit',frozenset({stage})),)
        rows=[]
        if stage=='OFFER':rows=[button('build','ساخت پیش‌نویس ثبت')]
        elif stage=='WRITE_CONFIRM':
            rows=[button('confirm_write','✅ تأیید و ثبت در Excel')]
            if self.current((str(actor),str(actor)))['status']=='TEXT_OK':
                rows.append(button('edit','بازگشت به اصلاح'))
        elif stage in ('TEXT_CONFIRM','REVIEW'):
            if stage=='TEXT_CONFIRM':rows.append(button('confirm_text','✅ تأیید صحت متن گزارش'))
            rows += [button('edit','✏️ اصلاح موارد'),button('dates','📅 انتخاب تاریخ'),button('sections','انتخاب بخش')]
            d=self.current((str(actor),str(actor)))
            for i,g in enumerate(d['payload']['groups']):
                if g['expected']:
                    rows += [button(f'append_{i}',f"{g['code']} — افزودن به شرح قبلی"),button(f'replace_{i}',f"{g['code']} — جایگزینی شرح قبلی")]
        elif stage=='ITEMS':
            rows=[button(f'item_{i}',f"مورد {i+1} — {item['code'] or 'دستگاه نامشخص'}") for i,item in enumerate(self.current((str(actor),str(actor)))['payload']['items'])]
            rows.append(button('review','بازگشت به پیش‌نمایش'))
        elif stage=='FIELD':
            rows=[button('code','اصلاح کد دستگاه'),button('description','اصلاح شرح'),
                  button('accept_raw','همین عبارت خام صحیح است'),button('exclude','خارج‌کردن صریح این مورد از ثبت'),button('edit','انتخاب مورد دیگر')]
        elif stage=='SECTION':
            rows=[button(section,LABELS[section]) for section in self.sections(actor)]
        elif stage=='DATE':
            rows=[button('date_'+date.replace('/',''),date) for date in s.get('dates',[])]
        elif stage=='PAUSED':rows=[button('resume','ادامه پیش‌نویس')]
        rows.append(button('pause','⏸ توقف و گفتگو'))
        rows.append(button('cancel','انصراف'))
        return InlineKeyboardBuilder(self.namespace,rows)

    async def reply(self,key,gateway,send,text,**kwargs):
        # Send the complete preview; only its final page carries the approval.
        chunks=[text[i:i+3500] for i in range(0,len(text),3500)] or ['']
        bot=self.lifecycle.bot(gateway)
        for chunk in chunks[:-1]:
            if bot:await bot.send_message(chat_id=key[1],text=chunk,parse_mode=None)
            else:send(gateway,key[1],chunk)
        await super().reply(key,gateway,send,chunks[-1],**kwargs)
        s=self.sessions.get(key)
        if s and s.get('draft') and s.get('message_id') and s.get('revision'):
            self.drafts.bind(self.current(key),s['stage'],s['revision'],s['message_id'])

    async def invoke(self,key,action,**fields):
        response=await self.run(dict(action=action,actor=key[0],chat=key[1],**fields))
        if not response['ok']:raise ValueError(response['message'])
        return response['result']

    async def show(self,key,gateway,send):
        s=self.sessions[key];d=self.current(key);p=d['payload']
        if d['status'] in ('COMMITTING','PARTIAL','DONE'):
            lines=['رسید ثبت روزانه — ثبت دستگاه‌ها مستقل و ترتیبی است.']
            successes=sum(r.get('status')=='succeeded' for r in d['results'].values())
            lines.append(f"{successes} ثبت موفق از {len(p['groups'])} دستگاه؛ {len(p['groups'])-successes} مورد حل‌نشده.")
            for g in p['groups']:
                r=d['results'].get(g['operation'],{})
                lines.append(f"{g['code']} | {g['date']} | {LABELS[g['section']]} | "+
                    ('✅ ثبت موفق — '+r['result']['sheet'] if r.get('status')=='succeeded' else 'نتیجه حل‌نشده/بررسی و بازیابی لازم: '+r.get('message','هنوز ثبت نشده'))+f"\nUUID: {g['operation']}")
            if d['status']=='DONE':
                await self.lifecycle.retire(key,gateway)
                self.sessions.pop(key);self.persist()
                message='\n'.join(lines)
                await self.reply(key,gateway,send,message)
                return
            s['stage']='WRITE_CONFIRM'
            await self.reply(key,gateway,send,'\n'.join(lines)+'\nبازیابی/ادامه فقط با همان UUIDها و اجازهٔ دکمه زیر انجام می‌شود.')
            return
        if d['status']=='TEXT_OK':
            s['stage']='WRITE_CONFIRM'
            await self.reply(key,gateway,send,f"آیا گزارش تأییدشده برای تاریخ {p['date']} در بخش {LABELS[p['section']]} فایل گزارش روزانه رانندگان۲ ذخیره شود؟\nنسخه {d['version']} — {d['hash'][:16]}\nتأیید متن هنوز هیچ نوشتنی انجام نداده است.")
            return
        s['stage']='REVIEW' if p['issues'] else 'TEXT_CONFIRM'
        unclear_date=any(i['item'] is None and i['field']=='date' for i in p['extraction']['unresolved']) and 'date' not in p['resolved']
        lines=[f"پیش‌نویس گزارش روزانه — نسخه {d['version']}\nتاریخ: {p['date'] or 'نامشخص'}"+
               (' (پیشنهاد امروز؛ با تأیید متن پذیرفته می‌شود)' if p['extraction']['report_date'] is None and not p.get('date_override') and not unclear_date else ''),
               'تاریخ در متن: '+(p['extraction']['report_date'] or ('نیازمند استخراج/انتخاب صریح' if unclear_date else 'ذکر نشده')),
               'بخش: '+LABELS.get(p['section'],'انتخاب صریح لازم است')]
        for i,item in enumerate(p['items']):
            source=p['extraction']['items'][i]['source_span']
            lines.append(f"\nمورد {i+1} | {item['code'] or 'بدون کد'} | موقعیت {source['start']}:{source['end']}"+
                         (' — از ثبت خارج شد' if item['excluded'] else '')+'\n'+item['description'])
        for g in p['groups']:
            lines.append(f"\n{g['name']} — {g['code']} | {g['date']} | {LABELS[g['section']]}\nشرح قبلی همان سلول:\n{g['expected'] or 'خالی'}\nمتن جدید:\n{g['description']}\nروش: {g['write_mode']}\nمتن نهایی پیشنهادی:\n{g['final_description']}")
        if p['issues']:lines.append('\nنیازمند تکمیل:\n'+'\n'.join(p['issues']))
        lines.append('\nتأیید صحت متن هیچ Write انجام نمی‌دهد. برای اصلاح هر مورد دکمهٔ اصلاح را بزنید.')
        await self.reply(key,gateway,send,'\n'.join(lines))

    async def advance_ai(self,key,command,text,gateway,send,*,reply_id=None):
        s=self.sessions[key]
        if not self.authorize(key[0]):raise PermissionError('مجوز فعلی ثبت وجود ندارد.')
        if command=='build':
            existing=self.drafts.latest(*key)
            if existing:
                s['draft']=existing['id']
                await self.invoke(key,'ai_resume',draft=s['draft'])
                await self.show(key,gateway,send)
                return
            try:
                extraction=await self.extractor(s['raw'],key[0])
            except Exception as exc:
                raise ValueError('مدل نتوانست پیش‌نویس معتبر آماده کند؛ هیچ اجازهٔ ثبت ایجاد نشده است. فرم دستی «شرح خرابی» در دسترس است.') from exc
            d=await self.invoke(key,'ai_create',extraction=extraction)
            s['draft']=d['id'];s.pop('raw',None);self.persist()
            await self.show(key,gateway,send);return
        if command=='resume':
            d=self.drafts.latest(*key)
            if not d:raise ValueError('پیش‌نویس باز وجود ندارد.')
            s['draft']=d['id']
            await self.invoke(key,'ai_resume',draft=s['draft'])
            # Read-only resume never retries a write. Existing approvals remain bound
            # to their version; a fresh callback is required to resume a partial save.
            await self.show(key,gateway,send);return
        if command in ('pause','cancel'):
            if s.get('draft'):
                d=self.current(key);d['binding']=None
                if command=='cancel' and d['status'] not in ('COMMITTING','PARTIAL','DONE'):
                    d.update(status='CANCELLED',text_ok=None,write_ok=None)
                self.drafts.update(d,'paused' if command=='pause' else 'cancel_requested')
            if command=='cancel' and (not s.get('draft') or self.current(key)['status']=='CANCELLED'):
                await self.lifecycle.retire(key,gateway)
                self.sessions.pop(key);self.persist()
                send(gateway,key[1],'پیش‌نویس لغو شد؛ هیچ اجازهٔ ثبت فعالی برای آن باقی نماند.')
                return
            s.update(stage='PAUSED',revision='');self.persist()
            await self.reply(key,gateway,send,'پیش‌نویس متوقف شد. برای ادامه «ادامه پیش‌نویس» را بفرستید. گفتگو دادهٔ فرم را تغییر نمی‌دهد.');return
        if command=='confirm_text':await self.show(key,gateway,send);return
        if command=='confirm_write':
            s['stage']='BUSY';self.persist()
            await self.invoke(key,'ai_commit',draft=s['draft'])
            await self.show(key,gateway,send);return
        if command=='edit':
            # Invalidate BOTH approvals immediately, even before a new value arrives.
            await self.invoke(key,'ai_edit',draft=s['draft'],field='review')
            s['stage']='ITEMS';await self.reply(key,gateway,send,'موردی که نیاز به اصلاح یا خارج‌کردن دارد انتخاب کنید.');return
        if command.startswith('item_'):
            s.update(stage='FIELD',index=int(command[5:]));await self.reply(key,gateway,send,'کدام قسمت این مورد اصلاح شود؟');return
        if command in ('code','description'):
            s.update(stage='INPUT',field=command)
            await self.reply(key,gateway,send,'مقدار اصلاح‌شده را با Reply به همین پیام بفرستید. پیام عادی وارد فرم نمی‌شود.');return
        field=value=index=None
        if command in ('exclude','accept_raw'):field=command;index=s['index']
        elif s['stage']=='INPUT' and command=='input':
            field=s['field'];value=text;index=s['index']
        elif command=='dates':
            dates=await self.invoke(key,'dates');s.update(stage='DATE',dates=dates['dates'])
            await self.reply(key,gateway,send,'📅 انتخاب تاریخ گزارش؛ تاریخ ذکرشده بدون انتخاب صریح عوض نمی‌شود.');return
        elif command=='sections':
            s['stage']='SECTION';await self.reply(key,gateway,send,'بخش مجاز گزارش را صریحاً انتخاب کنید.');return
        elif command in LABELS:field='section';value=command
        elif command.startswith('date_'):
            raw=command[5:];field='date';value=raw[:4]+'/'+raw[4:6]+'/'+raw[6:]
        elif command.startswith(('append_','replace_')):
            mode,position=command.split('_');field='mode';value=mode;index=self.current(key)['payload']['groups'][int(position)]['code']
        elif command=='review':await self.show(key,gateway,send);return
        if field:
            await self.invoke(key,'ai_edit',draft=s['draft'],field=field,value=value,index=index)
            await self.show(key,gateway,send)

    def handle(self,event,gateway,*,send,allow_start=True):
        source=event.source
        if str(getattr(source.platform,'value',source.platform)).lower()!='bale' or source.chat_type!='dm':return None
        actor,chat=str(source.user_id),str(source.chat_id);key=(actor,chat)
        if actor!=chat or not self.authorize(actor):return None
        text=event.text or '';clean=normalized(text);raw=getattr(event,'raw_message',None) or {}
        callback=isinstance(raw,dict) and raw.get('bale_inline_callback') is True
        if callback and not str(raw.get('data','')).startswith('ik:daily_ai:'):return None
        s=self.sessions.get(key)
        command=''
        if key in self.busy:
            if callback or clean in ('ادامه پیش نویس','توقف پیش نویس'):
                send(gateway,chat,'آماده‌سازی یا بازیابی قبلی در حال انجام است.');return {'action':'skip','reason':'daily-ai-busy'}
            return None
        if callback:
            try:
                if not s:raise ValueError('پیش‌نویس را دوباره باز کنید.')
                command=self.actor_keyboard(s['stage'],actor).resolve(raw['data'],s.get('revision',''),stage=s['stage'],role='',permits=lambda _:self.authorize(actor))
                if str(raw.get('origin_message_id'))!=s.get('message_id'):raise ValueError('پیام مبدأ معتبر نیست.')
                if s.get('draft'):
                    self.drafts.consume(s['draft'],actor,chat,s['stage'],s['revision'],s['message_id'],command)
                elif s.get('expires',0)<self.clock():raise ValueError('پیشنهاد منقضی شده است.')
                s['revision']='';self.persist()
            except (ValueError,PermissionError) as exc:
                send(gateway,chat,str(exc));return {'action':'skip','reason':'daily-ai-stale'}
        elif clean=='ادامه پیش نویس':
            if not s:self.sessions[key]=s=dict(stage='PAUSED',expires=self.clock()+1800,revision='')
            command='resume'
        elif clean=='توقف پیش نویس' and s:command='pause'
        elif s and s['stage']=='INPUT' and str(getattr(event,'reply_to_message_id',''))==s.get('message_id'):
            command='input'
        elif allow_start and intent(text)=='explicit' and s and s.get('draft'):
            send(gateway,chat,'پیش‌نویس باز دارید؛ ابتدا آن را تکمیل یا لغو کنید. متن پیام تازه در آن مصرف نمی‌شود.')
            command='resume'
        elif allow_start and intent(text)!='none' and not (s and s.get('draft')):
            self.sessions[key]=s=dict(stage='OFFER',raw=text,expires=self.clock()+1800,revision='')
            command='build' if intent(text)=='explicit' else 'offer'
        else:return None
        async def execute():
            try:
                if command=='offer':await self.reply(key,gateway,send,'این پیام می‌تواند گزارش روزانه باشد. در صورت تمایل پیش‌نویس ثبت بسازید.')
                else:await self.advance_ai(key,command,text,gateway,send)
            except Exception as exc:
                log.warning('Daily draft stopped: %s',type(exc).__name__)
                s=self.sessions.get(key)
                if s:s.update(stage='PAUSED',revision='');self.persist()
                send(gateway,chat,'گردش پیش‌نویس متوقف شد: '+str(exc)+'\nبرای بازیابی «ادامه پیش‌نویس»؛ فرم دستی با «شرح خرابی» در دسترس است.')
        self.start_task(key,execute(),gateway,send)
        return {'action':'skip','reason':'daily-ai'}


_handler=DailyAIHandler(state_store=StateStore(ROOT/'runtime/bale_ui/daily_ai.json'))
