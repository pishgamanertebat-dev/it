"""AI preparation inside the existing Repairs form/lifecycle, never an AI Writer."""
import logging

from tools.bale_ui import Action, InlineKeyboardBuilder, StateStore
from .entry_bale import RepairsEntryHandler, ROOT, LABELS, normalized, permitted, worker
from .entry_draft_store import DraftStore, ACTORS
from .entry_extract import extract, intent_decision

log = logging.getLogger(__name__)
HANDLER_VERSION = 'daily-ai-two-step-20261111.1'


def trace_route(text, reason=None):
    decision, evidence = intent_decision(text)
    log.info('Daily draft route handler=%s intent=%s reason=%s',
             HANDLER_VERSION, decision, reason or evidence)
    return decision


class DailyAIHandler(RepairsEntryHandler):
    namespace = 'daily_ai'
    entry_command = 'ادامه گزارش'

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
        if stage=='OFFER':rows=[button('build','تأیید گزارش')]
        elif stage in ('TEXT_CONFIRM','REVIEW'):
            if stage=='TEXT_CONFIRM':rows.append(button('confirm_text','تأیید متن'))
            rows += [button('edit','اصلاح گزارش')]
            d=self.current((str(actor),str(actor)))
            if any(g['expected'] and g['code'] not in d['payload']['modes'] for g in d['payload']['groups']):
                rows.append(button('conflicts','تعیین روش ثبت شرح‌های قبلی'))
        elif stage=='ITEMS':
            rows=[button('dates','انتخاب تاریخ'),button('sections','انتخاب بخش')]
            if any(g['expected'] for g in self.current((str(actor),str(actor)))['payload']['groups']):
                rows.append(button('conflicts','تعیین روش ثبت شرح‌های قبلی'))
            rows += [button(f'item_{i}',f"مورد {i+1} — {item['code'] or 'دستگاه نامشخص'}") for i,item in enumerate(self.current((str(actor),str(actor)))['payload']['items'])]
            rows.append(button('review','بازگشت به پیش‌نمایش'))
        elif stage=='CONFLICTS':
            rows=[button(f'mode_{i}',g['code']+' — '+({'append':'افزودن','replace':'جایگزینی'}.get(self.current((str(actor),str(actor)))['payload']['modes'].get(g['code']),'انتخاب لازم است')))
                  for i,g in enumerate(self.current((str(actor),str(actor)))['payload']['groups']) if g['expected']]
            rows.append(button('review','بازگشت به بررسی'))
        elif stage=='MODE':
            i=s['group_index']
            rows=[button(f'append_{i}','افزودن به شرح قبلی'),button(f'replace_{i}','جایگزینی شرح قبلی'),button('conflicts','سایر موارد دارای شرح قبلی')]
        elif stage=='FIELD':
            rows=[button('code','اصلاح کد دستگاه'),button('description','اصلاح شرح'),
                  button('accept_raw','متن این مورد درست است'),button('exclude','حذف این مورد از گزارش'),button('edit','انتخاب مورد دیگر')]
        elif stage=='SECTION':
            rows=[button(section,LABELS[section]) for section in self.sections(actor)]
        elif stage=='DATE':
            rows=[button('date_'+date.replace('/',''),date) for date in s.get('dates',[])]
        elif stage in ('PAUSED','RECOVERY'):rows=[button('resume','ادامه گزارش')]
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
            self.drafts.bind(self.current(key),s['stage'],s['revision'],s['message_id'],
                             write_intent=s['stage']=='TEXT_CONFIRM')

    async def invoke(self,key,action,**fields):
        response=await self.run(dict(action=action,actor=key[0],chat=key[1],**fields))
        if not response['ok']:raise ValueError(response['message'])
        return response['result']

    async def show(self,key,gateway,send):
        s=self.sessions[key];d=self.current(key);p=d['payload']
        if d['status'] in ('COMMITTING','PARTIAL','DONE'):
            lines=['گزارش ثبت شد.' if d['status']=='DONE' else 'نتیجهٔ ثبت نیازمند بررسی است.',
                   f"تاریخ: {p['date']}", 'بخش: '+LABELS[p['section']]]
            successes=sum(r.get('status')=='succeeded' for r in d['results'].values())
            lines.append(f"تعداد دستگاه‌های ثبت‌شده: {successes} از {len(p['groups'])}")
            for g in p['groups']:
                r=d['results'].get(g['operation'],{})
                lines.append(f"{g['code']} | {g['date']} | {LABELS[g['section']]} | "+
                    ('ثبت شد' if r.get('status')=='succeeded' else 'نتیجه حل‌نشده؛ بررسی لازم است.'))
            sent=any(r.get('result',{}).get('delivery',{}).get('status')=='correction_required' for r in d['results'].values())
            lines.append('PDF این تاریخ سابقهٔ ارسال یا نتیجهٔ نامطمئن دارد؛ نیاز به بررسی اصلاحیهٔ مستقل ثبت شد.' if sent else
                         'اگر PDF این تاریخ قبلاً ارسال شده، اصلاحیهٔ مستقل لازم است؛ ارسال مجدد انجام نمی‌شود.')
            if d['status']=='DONE':
                await self.lifecycle.retire(key,gateway)
                s['stage']='BUSY';self.persist()
                message='\n'.join(lines)
                await self.reply(key,gateway,send,message)
                self.sessions.pop(key);self.persist()
                return
            s['stage']='RECOVERY'
            await self.reply(key,gateway,send,'\n'.join(lines)+'\nادامه گزارش فقط نتیجهٔ ثبت قبلی را بررسی می‌کند.')
            return
        s['stage']='REVIEW' if p['issues'] else 'TEXT_CONFIRM'
        unclear_date=any(i['item'] is None and i['field']=='date' for i in p['extraction']['unresolved']) and 'date' not in p['resolved']
        lines=[f"بررسی گزارش\nتاریخ: {p['date'] or 'نامشخص'}"+
               (' (امروز؛ در صورت نیاز تاریخ را تغییر دهید)' if p['extraction']['report_date'] is None and not p.get('date_override') and not unclear_date else ''),
               'بخش: '+LABELS.get(p['section'],'انتخاب صریح لازم است')]
        for i,item in enumerate(p['items']):
            lines.append(f"\nمورد {i+1} | {item['code'] or 'بدون کد'}"+
                         (' — از ثبت خارج شد' if item['excluded'] else '')+'\n'+item['description'])
        for g in p['groups']:
            lines.append(f"\n{g['name']} — {g['code']} | {g['date']} | {LABELS[g['section']]}")
            if g['expected']:
                mode=p['modes'].get(g['code'])
                mode_label={'append':'افزودن به متن قبلی','replace':'جایگزینی متن قبلی'}.get(mode,'افزودن یا جایگزینی را انتخاب کنید')
                lines.append(f"متن قبلی:\n{g['expected']}\nروش ثبت: {mode_label}\nمتن پس از ثبت:\n{g['final_description']}")
                if g.get('duplicate_text'):
                    lines.append('این متن در شرح قبلی دیده می‌شود. افزودن، آن را دوباره درج می‌کند؛ روش ثبت را خودتان انتخاب کنید.')
        if p.get('notice'):lines.append('\n'+p['notice'])
        if p['issues']:lines.append('\nنیازمند تکمیل:\n'+'\n'.join(p['issues']))
        lines.append('\nگزارشت را بررسی کن. با انتخاب تأیید متن، اطلاعات در فایل گزارش ثبت می‌شوند.')
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
                log.warning('Daily draft stopped handler=%s reason=extraction_failed error=%s',
                            HANDLER_VERSION, type(exc).__name__)
                raise ValueError('گزارش آماده نشد. می‌توانید دوباره تلاش کنید یا از فرم دستی «شرح خرابی» استفاده کنید.') from exc
            d=await self.invoke(key,'ai_create',extraction=extraction)
            s['draft']=d['id'];s.pop('raw',None);self.persist()
            await self.show(key,gateway,send);return
        if command=='resume':
            d=self.current(key) if s.get('draft') else self.drafts.latest(*key)
            if not d:
                if s.get('raw'):
                    s['stage']='OFFER'
                    await self.reply(key,gateway,send,'گزارشت دریافت شد.\n\nبرای بررسی و ثبت گزارش، تأیید گزارش را انتخاب کن.');return
                raise ValueError('گزارشی برای ادامه وجود ندارد.')
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
                send(gateway,key[1],'ثبت گزارش لغو شد.')
                return
            s.update(stage='PAUSED',revision='');self.persist()
            message=('ادامهٔ ثبت متوقف شد. نتیجهٔ موارد ثبت‌شده حفظ شده است.' if command=='cancel' else 'گزارش برای ادامه نگه داشته شد.')
            await self.reply(key,gateway,send,message+' برای ادامه «ادامه گزارش» را بفرستید.');return
        if command=='confirm_text':
            s['stage']='BUSY';self.persist()
            await self.invoke(key,'ai_commit',draft=s['draft'])
            await self.show(key,gateway,send);return
        if command=='edit':
            # Invalidate BOTH approvals immediately, even before a new value arrives.
            await self.invoke(key,'ai_edit',draft=s['draft'],field='review')
            s['stage']='ITEMS';await self.reply(key,gateway,send,'موردی که نیاز به اصلاح یا خارج‌کردن دارد انتخاب کنید.');return
        if command=='conflicts':
            s['stage']='CONFLICTS'
            await self.reply(key,gateway,send,'فقط دستگاه‌های دارای شرح قبلی نمایش داده شده‌اند. روش ثبت هر دستگاه را جداگانه انتخاب کنید.');return
        if command.startswith('mode_'):
            i=int(command[5:]);s.update(stage='MODE',group_index=i)
            g=self.current(key)['payload']['groups'][i]
            await self.reply(key,gateway,send,f"{g['code']}\nشرح قبلی:\n{g['expected']}\nشرح تازه:\n{g['description']}\nروش ثبت همین دستگاه را انتخاب کنید.");return
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
        if actor!=chat or not self.authorize(actor):
            trace_route(event.text, 'actor_not_authorized')
            return None
        text=event.text or '';clean=normalized(text);raw=getattr(event,'raw_message',None) or {}
        callback=isinstance(raw,dict) and raw.get('bale_inline_callback') is True
        if callback and not str(raw.get('data','')).startswith('ik:daily_ai:'):return None
        s=self.sessions.get(key)
        if callback and not s:
            existing=self.drafts.latest(*key)
            if existing:
                self.sessions[key]=s=dict(stage='PAUSED',revision='',expires=self.clock()+1800,draft=existing['id'])
        decision = trace_route(text, 'callback' if callback else ('manual_form_open' if not allow_start else None))
        command=''
        if key in self.busy:
            if callback or clean in ('ادامه گزارش','ادامه پیش نویس','توقف پیش نویس'):
                send(gateway,chat,'آماده‌سازی یا بازیابی قبلی در حال انجام است.');return {'action':'skip','reason':'daily-ai-busy'}
            return None
        if callback:
            try:
                if not s:raise ValueError('گزارش را دوباره باز کنید.')
                if str(raw.get('data','')).rsplit(':',1)[-1] in ('confirm_write','pause'):
                    raise ValueError('دکمهٔ قبلی معتبر نیست؛ گزارش را در صفحهٔ جدید بررسی کنید.')
                command=self.actor_keyboard(s['stage'],actor).resolve(raw['data'],s.get('revision',''),stage=s['stage'],role='',permits=lambda _:self.authorize(actor))
                if str(raw.get('origin_message_id'))!=s.get('message_id'):raise ValueError('پیام مبدأ معتبر نیست.')
                if s.get('draft'):
                    self.drafts.consume(s['draft'],actor,chat,s['stage'],s['revision'],s['message_id'],command)
                elif s.get('expires',0)<self.clock():raise ValueError('پیشنهاد منقضی شده است.')
                s['revision']='';self.persist()
            except (ValueError,PermissionError) as exc:
                log.info('Daily draft stopped handler=%s reason=stale_callback error=%s',
                         HANDLER_VERSION, type(exc).__name__)
                send(gateway,chat,str(exc))
                if s and s.get('draft'):
                    self.start_task(key,self.advance_ai(key,'resume','',gateway,send),gateway,send)
                elif s and s.get('raw'):
                    s.update(stage='OFFER',revision='')
                    self.start_task(key,self.reply(key,gateway,send,'گزارشت دریافت شد.\n\nبرای بررسی و ثبت گزارش، تأیید گزارش را انتخاب کن.'),gateway,send)
                return {'action':'skip','reason':'daily-ai-stale'}
        elif clean in ('ادامه گزارش','ادامه پیش نویس'):
            if not s:self.sessions[key]=s=dict(stage='PAUSED',expires=self.clock()+1800,revision='')
            command='resume'
        elif clean=='توقف پیش نویس' and s:command='pause'
        elif s and s['stage']=='INPUT' and str(getattr(event,'reply_to_message_id',''))==s.get('message_id'):
            command='input'
        elif allow_start and decision=='explicit' and s and s.get('draft'):
            send(gateway,chat,'یک گزارش باز دارید. ابتدا آن را ثبت یا لغو کنید.')
            command='resume'
        elif allow_start and decision!='none' and not (s and s.get('draft')):
            self.sessions[key]=s=dict(stage='OFFER',raw=text,expires=self.clock()+1800,revision='')
            command='offer'
        else:return None
        async def execute():
            try:
                if command=='offer':await self.reply(key,gateway,send,'گزارشت دریافت شد.\n\nبرای بررسی و ثبت گزارش، تأیید گزارش را انتخاب کن.')
                else:await self.advance_ai(key,command,text,gateway,send)
            except Exception as exc:
                log.warning('Daily draft stopped handler=%s reason=workflow_failed action=%s error=%s',
                            HANDLER_VERSION, command, type(exc).__name__)
                s=self.sessions.get(key)
                if s:s.update(stage='PAUSED',revision='');self.persist()
                send(gateway,chat,'ثبت گزارش متوقف شد: '+str(exc)+'\nبرای ادامه «ادامه گزارش» را بفرستید. فرم دستی «شرح خرابی» هم در دسترس است.')
        self.start_task(key,execute(),gateway,send)
        return {'action':'skip','reason':'daily-ai'}


_handler=DailyAIHandler(state_store=StateStore(ROOT/'runtime/bale_ui/daily_ai.json'))
