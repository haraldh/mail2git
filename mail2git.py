import mailbox
import sys
from sets import Set
from operator import itemgetter
import time
import email.utils
import re
import os
from git import Repo
import atexit
import shutil
import smtplib
from email.mime.text import MIMEText

from mail2gitconfig import *

if email_to:
    smtp = smtplib.SMTP('localhost')

MAIL2GIT_VARDIR = "/var/tmp/mail2git-%d" % os.getpid()

mid = {}
kid = {}
threads = {}
patch_pattern = re.compile(r'[PATCH.*[^\]]*\s*\d+/(\d+)\s*\]')
diff_pattern = re.compile(r'^index.*\n---.*\n\+\+\+.*\n@@ .*', re.MULTILINE)

def check_complete(thread):
    max = 0
    for i in thread:
        s = mid[i]['Subject'].replace('\n', ' ').replace('\r', '')
        #print "Checking %s" % s
        m = patch_pattern.search(s)
        if m:
            max = int(m.group(1))
            break
    else:
        return [ thread[0] ]

    if max < 1:
        return None

    ret = []

    for n in range(1,max+1):
        f = '\[PATCH.*%d\/%d\s*\]' % (n, max)
        fp = re.compile(f)
        #print len(thread)
        for i in thread:
            s = mid[i]['Subject'].replace('\n', ' ').replace('\r', '')
            if fp.search(s):
                #print "Found '%s' in %s" % (f, s)
                ret.append(i)
                break
        else:
            #print "Not Found in %s" % f
            return None

    return ret

def cleanup_maildir():
    shutil.rmtree(MAIL2GIT_VARDIR, ignore_errors=True)

atexit.register(cleanup_maildir)

inbox = mailbox.mbox(mailbox_file)

def inbox_unlock():
    global inbox
    inbox.flush()
    inbox.unlock()

atexit.register(inbox_unlock)
inbox.lock()

for key in inbox.iterkeys():
    try:
        message = inbox[key]
    except email.errors.MessageParseError:
        inbox.discard(key)
        continue

    i = message['Message-ID']
    mid[i] = message
    kid[i] = key

    if message['subject'].find("[PATCH") == -1:
        inbox.discard(key)
        continue

    # search also with MIME decode
    if not diff_pattern.search(message.as_string()):
        if message.is_multipart():
            for m in message.get_payload(decode=True) or []:
                if diff_pattern.search(m.get_payload(decode=True)):
                    break
            else:
                inbox.discard(key)
                continue
        else:
            if not diff_pattern.search(message.get_payload(decode=True)):
                inbox.discard(key)
                continue

    s = message['Subject'].replace('\n', ' ').replace('\r', '')

    if message.has_key('In-Reply-To') and patch_pattern.search(s):
        if not threads.has_key(message['In-Reply-To']):
            threads[message['In-Reply-To']] = Set()
            threads[message['In-Reply-To']].add(message['In-Reply-To'])
        threads[message['In-Reply-To']].add(i)
        #print "Adding %s" % s
        continue

    if patch_pattern.match(s) and not re.search("\[PATCH.*[^\]]*\s*1/(\d+)\s*\]", s):
        continue

    if not threads.has_key(i):
        threads[i] = Set()

    #print "Adding %s" % s
    threads[i].add(i)

# remove all threads without the starting message
# remove all threads with incomplete patch set
for t in threads.keys():
    if not mid.has_key(t):
        #print "Removing %s" % t
        del threads[t]
        continue

# sort threads by date
for t in threads.keys():
    l = [(email.utils.mktime_tz(email.utils.parsedate_tz(mid[t]['date'])), f) for f in threads[t]]
    l.sort(key=itemgetter(1))
    threads[t] = [ b for a, b in l ]

for t in threads.keys():
    ret = check_complete(threads[t])
    if not ret:
        #print "Deleting %s" % t
        del threads[t]
        continue

    #print "Saving %s" % ret
    threads[t] = ret

if not os.path.isdir(MAIL2GIT_VARDIR):
    os.mkdir(MAIL2GIT_VARDIR)

# print threads
for t in threads.keys():
    lastmid = threads[t][-1][1:-1]
    mboxfile = '%s/%s' % (MAIL2GIT_VARDIR, lastmid)
    if not os.path.isfile(mboxfile):
        box = mailbox.mbox(mboxfile)
        box.lock()

        for b in threads[t]:
            message = mid[b]
            box.add(message)
            #print message['subject']

        box.flush()
        box.unlock()
        box.close()

    for b in threads[t]:
        key = kid[b]
        inbox.discard(key)

repo = Repo(".")
git = repo.git

repo.heads.master.checkout()
git.pull("--all", "--prune")

for t in threads.keys():
    lastmid = threads[t][-1][1:-1]
    lastsubject = mid[threads[t][-1]]['Subject']
    mboxfile = '%s/%s' % (MAIL2GIT_VARDIR, lastmid)
    if not os.path.isfile(mboxfile):
        continue

    # check if branch already exists
    if lastmid in repo.heads or "refs/remotes/origin/%s" % lastmid in repo.refs:
        #repo.delete_head(lastmid, "-D")
        print "Branch %s does already exist" % lastmid
        continue

    repo.heads.master.checkout()
    new_branch = repo.create_head(lastmid, repo.heads.master)
    new_branch.checkout()

    try:
        #if os.system("git am %s" % mboxfile) != 0:
        #    raise Exception("test")
        git.am(mboxfile, "--scissors")
        print "[OK]     %s" % lastmid
    except:
        print "[FAILED] %s" % lastmid
        try:
            git.am("--abort")
        except:
            pass
        repo.heads.master.checkout()
        repo.delete_head(lastmid, "-D")
        continue
    else:
        if email_to:
            msg = MIMEText(email_message % lastmid.replace("@", "%40"))
            msg.add_header('In-Reply-To', '<' + lastmid + '>')
            msg.add_header('References', '<' + lastmid + '>')
            msg['From'] = email_from
            msg['To' ] = email_to
            msg['Subject'] = "Re: " + lastsubject
            msg['Date'] = email.utils.formatdate()
            msg['Message-ID'] = email.utils.make_msgid('githubbot')
            smtp.sendmail(email_from, [email_to], msg.as_string())


smtp.quit()

repo.heads.master.checkout()
git.push("--all")

# Now remove all messages older than a day

for key in inbox.iterkeys():
    message = inbox[key]
    mtime = email.utils.mktime_tz(email.utils.parsedate_tz(message['date']))
    ltime = time.time()
    if (ltime - mtime) > 86400:
        inbox.discard(key)
