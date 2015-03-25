import mailbox
import sys
from sets import Set
from operator import itemgetter, attrgetter, methodcaller
import time
import email.utils
import re
import os
from git import Repo

MAIL2GIT_VARDIR = "/var/tmp/mail2git"

mid = {}
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

for message in mailbox.mbox(sys.argv[1]):
    i = message['Message-ID']
    mid[i] = message

    if message['subject'].find("[PATCH") == -1:
        continue

    # search also with MIME decode
    if not diff_pattern.search(message.as_string()):
        if message.is_multipart():
            for m in message.get_payload(decode=True) or []:
                if diff_pattern.search(m.get_payload(decode=True)):
                    break
            else:
                continue
        else:
            if not diff_pattern.search(message.get_payload(decode=True)):
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
        print "MEH!!! : " + str(message)
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

repo = Repo(".")
git = repo.git

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
            print message['subject']

        box.flush()
        box.unlock()

#exit(0)

for t in threads.keys():
    lastmid = threads[t][-1][1:-1]
    mboxfile = '%s/%s' % (MAIL2GIT_VARDIR, lastmid)
    if not os.path.isfile(mboxfile):
        continue

    if lastmid in repo.heads:
        #repo.delete_head(lastmid, "-D")
        continue

    repo.heads.master.checkout()
    new_branch = repo.create_head(lastmid, repo.heads.master)
    new_branch.checkout()

    try:
        if os.system("git am --scissors %s" % mboxfile) != 0:
            raise Exception("test")
        #git.am(mboxfile, "--scissors")
        print "[OK]     %s" % lastmid
    except:
        print "[FAILED] %s" % lastmid
        git.am("--abort")
        repo.heads.master.checkout()
        repo.delete_head(lastmid, "-D")
        continue

repo.heads.master.checkout()
