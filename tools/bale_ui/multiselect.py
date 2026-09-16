"""Reusable staged multi-selection. This component never performs domain mutations."""
from dataclasses import asdict, dataclass, field

from .core import Action, InlineKeyboardBuilder


@dataclass
class MultiSelect:
    options: list[dict]
    selected: list[str] = field(default_factory=list)
    page: int = 0
    page_size: int = 10

    def __post_init__(self):
        ids = [o['id'] for o in self.options]
        if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('Selection options require unique nonempty string IDs')
        if not 1 <= self.page_size <= 20 or not 0 <= self.page < self.pages:
            raise ValueError('Invalid selection page')
        if len(set(self.selected)) != len(self.selected) or not set(self.selected) <= set(ids):
            raise ValueError('Invalid selected IDs')

    @property
    def pages(self):
        return max(1, (len(self.options) + self.page_size - 1) // self.page_size)

    def to_dict(self):
        return asdict(self)

    def apply(self, action):
        if action.startswith('pick_'):
            index = int(action[5:])
            if not self.page*self.page_size <= index < min(len(self.options), (self.page+1)*self.page_size):
                raise ValueError('Option is not on the current page')
            identifier = self.options[index]['id']
            selected = set(self.selected)
            selected.symmetric_difference_update({identifier})
            self.selected = [o['id'] for o in self.options if o['id'] in selected]
        elif action == 'select_next' and self.page + 1 < self.pages:
            self.page += 1
        elif action == 'select_prev' and self.page > 0:
            self.page -= 1
        else:
            raise ValueError('Invalid selection action')

    def confirmed_ids(self, current_ids):
        if list(current_ids) != [o['id'] for o in self.options]:
            raise ValueError('فهرست تغییر کرده است؛ به پیشنهاد برگردید و دوباره انتخاب کنید.')
        if not self.selected:
            raise ValueError('ابتدا حداقل یک گزینه انتخاب کنید.')
        return frozenset(self.selected)

    def keyboard(self, namespace, *, permission, stage, roles=frozenset(),
                 confirm_label='تأیید', back_label='بازگشت', selected_mark='✅', unselected_mark='▫️'):
        def button(name, label, refresh=False):
            return Action(name, label, permission, frozenset({stage}), roles, refresh=refresh)
        start = self.page * self.page_size
        rows = [(button(f'pick_{index}',
                        f"{selected_mark if option['id'] in self.selected else unselected_mark} {option['label']}", True),)
                for index, option in enumerate(self.options[start:start+self.page_size], start)]
        pages = []
        if self.page:
            pages.append(button('select_prev', '◀️ قبلی', True))
        if self.page + 1 < self.pages:
            pages.append(button('select_next', 'بعدی ▶️', True))
        if pages:
            rows.append(tuple(pages))
        rows.append((button('select_confirm', f'{confirm_label} ({len(self.selected)})', not self.selected),
                     button('select_back', back_label)))
        return InlineKeyboardBuilder(namespace, rows)
