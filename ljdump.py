#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# ljdump.py - livejournal archiver
# Greg Hewgill <greg@hewgill.com> https://hewgill.com/
# Version 1.5.1
#
# LICENSE
#
# This software is provided 'as-is', without any express or implied
# warranty.  In no event will the author be held liable for any damages
# arising from the use of this software.
#
# Permission is granted to anyone to use this software for any purpose,
# including commercial applications, and to alter it and redistribute it
# freely, subject to the following restrictions:
#
# 1. The origin of this software must not be misrepresented; you must not
#    claim that you wrote the original software. If you use this software
#    in a product, an acknowledgment in the product documentation would be
#    appreciated but is not required.
# 2. Altered source versions must be plainly marked as such, and must not be
#    misrepresented as being the original software.
# 3. This notice may not be removed or altered from any source distribution.
#
# Copyright (c) 2005-2010 Greg Hewgill and contributors

import argparse, codecs, os, pickle, pprint, re, shutil, sys, urllib2, xml.dom.minidom, xmlrpclib
from getpass import getpass
import urllib
from xml.sax import saxutils
from datetime import *


MimeExtensions = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

def flatresponse(response):
    r = {}
    while True:
        name = response.readline()
        if len(name) == 0:
            break
        if name[-1] == '\n':
            name = name[:len(name)-1]
        value = response.readline()
        if value[-1] == '\n':
            value = value[:len(value)-1]
        r[name] = value
    return r

def getljsession(server, username, password):
    """Log in with password and get session cookie."""
    qs = "mode=sessiongenerate&user=%s&auth_method=clear&password=%s" % (urllib.quote(username), urllib.quote(password))
    r = urllib2.urlopen(server+"/interface/flat", qs)
    response = flatresponse(r)
    r.close()
    return response['ljsession']

def dumpelement(f, name, e):
    f.write("<%s>\n" % name)
    for k in e.keys():
        if isinstance(e[k], {}.__class__):
            dumpelement(f, k, e[k])
        else:
            try:
                s = unicode(str(e[k]), "UTF-8")
            except UnicodeDecodeError:
                # fall back to Latin-1 for old entries that aren't UTF-8
                s = unicode(str(e[k]), "cp1252")
            f.write("<%s>%s</%s>\n" % (k, saxutils.escape(s), k))
    f.write("</%s>\n" % name)

def writedump(fn, event):
    f = codecs.open(fn, "w", "UTF-8")
    f.write("""<?xml version="1.0"?>\n""")
    dumpelement(f, "event", event)
    f.close()

def writelast(journal, lastsync, lastmaxid):
    f = open("%s/.last" % journal, "w")
    f.write("%s\n" % lastsync)
    f.write("%s\n" % lastmaxid)
    f.close()

def createxml(doc, name, map):
    e = doc.createElement(name)
    for k in map.keys():
        me = doc.createElement(k)
        me.appendChild(doc.createTextNode(map[k]))
        e.appendChild(me)
    return e

def gettext(e):
    if len(e) == 0:
        return ""
    return e[0].firstChild.nodeValue

def ljdump(Server, Username, Password, Journal, verbose=True):
    m = re.search("(.*)/interface/xmlrpc", Server)
    if m:
        Server = m.group(1)
    if Username != Journal:
        authas = "&authas=%s" % Journal
    else:
        authas = ""

    if verbose:
        print("Fetching journal entries for: %s" % Journal)
    try:
        os.mkdir(Journal)
        print "Created subdirectory: %s" % Journal
    except:
        pass

    ljsession = getljsession(Server, Username, Password)

    server = xmlrpclib.ServerProxy(Server+"/interface/xmlrpc")

    def authed(params):
        """Transform API call params to include authorization."""
        return dict(auth_method='clear', username=Username, password=Password, **params)

    newentries = 0
    newcomments = 0
    errors = 0

    lastsync = ""
    lastmaxid = 0
    try:
        f = open("%s/.last" % Journal, "r")
        lastsync = f.readline()
        if lastsync[-1] == '\n':
            lastsync = lastsync[:len(lastsync)-1]
        lastmaxid = f.readline()
        if len(lastmaxid) > 0 and lastmaxid[-1] == '\n':
            lastmaxid = lastmaxid[:len(lastmaxid)-1]
        if lastmaxid == "":
            lastmaxid = 0
        else:
            lastmaxid = int(lastmaxid)
        f.close()
    except:
        pass
    origlastsync = lastsync

    r = server.LJ.XMLRPC.login(authed({
        'ver': 1,
        'getpickws': 1,
        'getpickwurls': 1,
    }))
    userpics = dict(zip(map(str, r['pickws']), r['pickwurls']))
    if r['defaultpicurl']:
        userpics['*'] = r['defaultpicurl']

    while True:
        r = server.LJ.XMLRPC.syncitems(authed({
            'ver': 1,
            # 'lastsync': lastsync, # this one is not helpful when you want update existing stuff
            'usejournal': Journal,
        }))
        if len(r['syncitems']) == 0:
            break
        for item in r['syncitems']:
            if item['item'][0] == 'L':
                print "Fetching journal entry %s (%s)" % (item['item'], item['action'])
                try:
                    e = server.LJ.XMLRPC.getevents(authed({
                        'ver': 1,
                        'selecttype': "one",
                        'itemid': item['item'][2:],
                        'usejournal': Journal,
                    }))
                    if e['events']:
                        ev = e['events'][0]
                        newentries += 1

                        # Process the event
                        pprint.pprint(ev)
                        ev['event'] = re.sub('http://(edu.|staff.|)mmcs.sfedu.ru/~ulysses',
                                             'https://a-pelenitsyn.github.io/Files',
                                             str(ev['event']))

                        # Store locally
                        writedump("%s/%s" % (Journal, item['item']), ev)

                        # Write back the event to server
                        d = datetime.strptime(ev['eventtime'], '%Y-%m-%d %H:%M:%S')
                        ev1 = dict(lineendings="pc", year=d.year, mon=d.month, day=d.day,
                                   hour=d.hour, min=d.minute, **ev)
                        r1 = server.LJ.XMLRPC.editevent(authed(ev1))
                    else:
                        print "Unexpected empty item: %s" % item['item']
                        errors += 1
                except xmlrpclib.Fault, x:
                    print "Error getting item: %s" % item['item']
                    pprint.pprint(x)
                    errors += 1
            lastsync = item['time']
            writelast(Journal, lastsync, lastmaxid)
        print "Good now, bye!"
        os._exit(os.EX_OK)

    # The following code doesn't work because the server rejects our repeated calls.
    # https://www.livejournal.com/doc/server/ljp.csp.xml-rpc.getevents.html
    # contains the statement "You should use the syncitems selecttype in
    # conjuntions [sic] with the syncitems protocol mode", but provides
    # no other explanation about how these two function calls should
    # interact. Therefore we just do the above slow one-at-a-time method.

    #while True:
    #    r = server.LJ.XMLRPC.getevents(authed({
    #        'ver': 1,
    #        'selecttype': "syncitems",
    #        'lastsync': lastsync,
    #    }))
    #    pprint.pprint(r)
    #    if len(r['events']) == 0:
    #        break
    #    for item in r['events']:
    #        writedump("%s/L-%d" % (Journal, item['itemid']), item)
    #        newentries += 1
    #        lastsync = item['eventtime']

    if verbose:
        print("Fetching journal comments for: %s" % Journal)

    try:
        f = open("%s/comment.meta" % Journal)
        metacache = pickle.load(f)
        f.close()
    except:
        metacache = {}

    try:
        f = open("%s/user.map" % Journal)
        usermap = pickle.load(f)
        f.close()
    except:
        usermap = {}

    maxid = lastmaxid
    while True:
        try:
            try:
                r = urllib2.urlopen(urllib2.Request(Server+"/export_comments.bml?get=comment_meta&startid=%d%s" % (maxid+1, authas), headers = {'Cookie': "ljsession="+ljsession}))
                meta = xml.dom.minidom.parse(r)
            except Exception, x:
                print "*** Error fetching comment meta, possibly not community maintainer?"
                print "***", x
                break
        finally:
            try:
                r.close()
            except AttributeError: # r is sometimes a dict for unknown reasons
                pass
        for c in meta.getElementsByTagName("comment"):
            id = int(c.getAttribute("id"))
            metacache[id] = {
                'posterid': c.getAttribute("posterid"),
                'state': c.getAttribute("state"),
            }
            if id > maxid:
                maxid = id
        for u in meta.getElementsByTagName("usermap"):
            usermap[u.getAttribute("id")] = u.getAttribute("user")
        if maxid >= int(meta.getElementsByTagName("maxid")[0].firstChild.nodeValue):
            break

    f = open("%s/comment.meta" % Journal, "w")
    pickle.dump(metacache, f)
    f.close()

    f = open("%s/user.map" % Journal, "w")
    pickle.dump(usermap, f)
    f.close()

    newmaxid = maxid
    maxid = lastmaxid
    while True:
        try:
            try:
                r = urllib2.urlopen(urllib2.Request(Server+"/export_comments.bml?get=comment_body&startid=%d%s" % (maxid+1, authas), headers = {'Cookie': "ljsession="+ljsession}))
                meta = xml.dom.minidom.parse(r)
            except Exception, x:
                print "*** Error fetching comment body, possibly not community maintainer?"
                print "***", x
                break
        finally:
            r.close()
        for c in meta.getElementsByTagName("comment"):
            id = int(c.getAttribute("id"))
            jitemid = c.getAttribute("jitemid")
            comment = {
                'id': str(id),
                'parentid': c.getAttribute("parentid"),
                'subject': gettext(c.getElementsByTagName("subject")),
                'date': gettext(c.getElementsByTagName("date")),
                'body': gettext(c.getElementsByTagName("body")),
                'state': metacache[id]['state'],
            }
            if usermap.has_key(c.getAttribute("posterid")):
                comment["user"] = usermap[c.getAttribute("posterid")]
            try:
                entry = xml.dom.minidom.parse("%s/C-%s" % (Journal, jitemid))
            except:
                entry = xml.dom.minidom.getDOMImplementation().createDocument(None, "comments", None)
            found = False
            for d in entry.getElementsByTagName("comment"):
                if int(d.getElementsByTagName("id")[0].firstChild.nodeValue) == id:
                    found = True
                    break
            if found:
                print "Warning: downloaded duplicate comment id %d in jitemid %s" % (id, jitemid)
            else:
                entry.documentElement.appendChild(createxml(entry, "comment", comment))
                f = codecs.open("%s/C-%s" % (Journal, jitemid), "w", "UTF-8")
                entry.writexml(f)
                f.close()
                newcomments += 1
            if id > maxid:
                maxid = id
        if maxid >= newmaxid:
            break

    lastmaxid = maxid

    writelast(Journal, lastsync, lastmaxid)

    if Username == Journal:
        if verbose:
            print("Fetching userpics for: %s" % Username)
        f = open("%s/userpics.xml" % Username, "w")
        print >>f, """<?xml version="1.0"?>"""
        print >>f, "<userpics>"
        for p in userpics:
            print >>f, """<userpic keyword="%s" url="%s" />""" % (p, userpics[p])
            pic = urllib2.urlopen(userpics[p])
            ext = MimeExtensions.get(pic.info()["Content-Type"], "")
            picfn = re.sub(r'[*?\\/:<>"|]', "_", p)
            try:
                picfn = codecs.utf_8_decode(picfn)[0]
                picf = open("%s/%s%s" % (Username, picfn, ext), "wb")
            except:
                # for installations where the above utf_8_decode doesn't work
                picfn = "".join([ord(x) < 128 and x or "_" for x in picfn])
                picf = open("%s/%s%s" % (Username, picfn, ext), "wb")
            shutil.copyfileobj(pic, picf)
            pic.close()
            picf.close()
        print >>f, "</userpics>"
        f.close()

    if verbose or (newentries > 0 or newcomments > 0):
        if origlastsync:
            print("%d new entries, %d new comments (since %s)" % (newentries, newcomments, origlastsync))
        else:
            print("%d new entries, %d new comments" % (newentries, newcomments))
    if errors > 0:
        print "%d errors" % errors

if __name__ == "__main__":
    args = argparse.ArgumentParser(description="Livejournal archive utility")
    args.add_argument("--quiet", "-q", action='store_false', dest='verbose',
                      help="reduce log output")
    args = args.parse_args()
    if os.access("ljdump.config", os.F_OK):
        config = xml.dom.minidom.parse("ljdump.config")
        server = config.documentElement.getElementsByTagName("server")[0].childNodes[0].data
        username = config.documentElement.getElementsByTagName("username")[0].childNodes[0].data
        password_els = config.documentElement.getElementsByTagName("password")
        if len(password_els) > 0:
            password = password_els[0].childNodes[0].data
        else:
            password = getpass("Password: ")
        journals = [e.childNodes[0].data for e in config.documentElement.getElementsByTagName("journal")]
        if not journals:
            journals = [username]
    else:
        print "ljdump - livejournal archiver"
        print
        default_server = "https://livejournal.com"
        server = raw_input("Alternative server to use (e.g. 'https://www.dreamwidth.org'), or hit return for '%s': " % default_server) or default_server
        print
        print "Enter your Livejournal username and password."
        print
        username = raw_input("Username: ")
        password = getpass("Password: ")
        print
        print "You may back up either your own journal, or a community."
        print "If you are a community maintainer, you can back up both entries and comments."
        print "If you are not a maintainer, you can back up only entries."
        print
        journal = raw_input("Journal to back up (or hit return to back up '%s'): " % username)
        print
        if journal:
            journals = [journal]
        else:
            journals = [username]

    for journal in journals:
        ljdump(server, username, password, journal, args.verbose)
# vim:ts=4 et:	
