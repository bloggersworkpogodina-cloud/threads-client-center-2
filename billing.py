from datetime import datetime, timedelta
import calendar

def parse_date(v): return datetime.strptime(v, "%Y-%m-%d").date()
def fmt(d): return d.strftime("%d.%m.%Y")
def add_month(d, n=1):
    x=d.month-1+n; y=d.year+x//12; m=x%12+1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y,m)[1]))
def period(v, offset=0):
    base=parse_date(v); start=add_month(base,offset); nxt=add_month(base,offset+1)
    return start, nxt-timedelta(days=1), start-timedelta(days=7)
