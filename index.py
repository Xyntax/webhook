#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import re
import json
from flask import Flask, request, abort, send_file,render_template_string,session,redirect
from flask_httpauth import HTTPBasicAuth
import md5
import logging
import logging.handlers
from multiprocessing.pool import ThreadPool
import time
import subprocess

pool = ThreadPool(processes=5)

app = Flask(__name__)
app.config['SECRET_KEY'] = '上线之后需要修改，网管ヽ(￣▽￣)'
app.debug = True
auth = HTTPBasicAuth()

logs = {}

def getlog(filename):
    log = logging.getLogger(filename)
    log.setLevel(logging.INFO)
    log_handler = logging.handlers.RotatingFileHandler(filename + ".log",
                                                       maxBytes=4086,
                                                       backupCount=1024)
    f = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                          "%Y-%m-%d %H:%M:%S")
    log_handler.setFormatter(f)
    log.addHandler(log_handler)
    return log

webhooklog = getlog("/var/webhook/webhook")

@auth.verify_password
def verify_pw(repo, password):
    repos = json.loads(open('repos.json','rb').read())
    if repos.get(repo):
        if repos.get(repo).get('pass') == md5.md5(password+app.config['SECRET_KEY']).hexdigest():
            session['repo'] = repo
            if not logs.get(repo):
                logs[repo] = getlog(repo)
            return True
    return False
    


def build(name,url,branch,log):
    log.info('building %s:%s' % (name,branch))
    #check if need git init
    basedir = os.path.join(app.root_path,name)

    if not os.path.isdir(basedir):
        r = os.system("git clone %s" % url)
        if r != 0:
            log.critical('%s clone error' % name)
            return
        else:
            log.info('%s clone ok' % name)
    env = 'GIT_DIR="%s/.git" GIT_WORK_TREE="%s/" ' % (basedir,basedir)
    #change branch
    r = os.system(env+"git checkout master && "+env+"git pull && "+env+" git checkout %s" % branch)
    if r != 0:
        log.critical('%s:%s checkout error' % (name,branch))
    else:
        log.info('%s:%s checkout ok' % (name,branch))
        r = os.system(env+"git pull origin %s:%s" % (branch,branch))
        if r != 0:
            log.critical('%s:%s pull error' % (name,branch))
        else:
            log.info('%s:%s pull ok' % (name,branch))
            outpath = os.path.join(
                app.root_path,"outfile/%s/%s/" % (
                    name,
                    branch
                    )
                )
            if not os.path.isdir(outpath):
                os.makedirs(outpath,0755)
            args = ['zip','-r',os.path.join(outpath,str(int(time.time()))+'.zip')]
            if os.path.isfile(os.path.join(basedir,'build.json')):
                b = json.loads(open(os.path.join(basedir,'build.json')).read())
                for x in b.get('include',[basedir]):
                    args.append(x)

                for x in b.get('exclude',[]):
                    args.append("-x")
                    args.append(x)
                p = subprocess.Popen(args,cwd=basedir)
                timeout = 2
                while timeout>0:
                    timeout-=1
                    if p.poll():
                        break
                    time.sleep(1)
                if not p.poll():
                    try:
                        p.kill()
                    except:
                        pass
                r = p.returncode
                if r != 0:
                    log.critical('%s:%s build error' % (name,branch))
                else:
                    log.info('%s:%s build ok' % (name,branch))
            else:
                log.critical('%s:%s build error' % (name,branch))

    

@app.route("/push", methods=['GET', 'POST'])
def push():
    if request.method == 'GET':
        return 'OK'
    elif request.method == 'POST' and request.host.split(":")[0]=="webhook.ssctf.seclover.com":
        post = json.loads(request.data)
        if 'repository' not in post:
            abort(403)
        repos = json.loads(open('repos.json').read())
        name = post['repository']["name"]
        

        match = re.match(r"refs/heads/(?P<branch>.*)", post['ref'])
        if match:
            branch = match.groupdict()['branch']
            before = post.get("before") or ""
            msg = "recived push repo:{name} with before \n"
            msg += json.dumps(before,indent=4)
            webhooklog.info(msg.format(**locals()))
        else:
            abort(403)
        if repos.get(name):
            if not logs.get(name):
                logs[name] = getlog(name)
            url = repos[name].get("url","")
            log = logs[name]
            pool.apply_async(build, (name,url,branch,log))

        else:
            abort(403)
            
    return 'OK'

@app.route('/', defaults={'req_path': ''})
@app.route('/<path:req_path>')
@auth.login_required 
def dir_listing(req_path):
    BASE_DIR = os.path.join(app.root_path,'outfile',session['repo'])

    # Joining the base and the requested path
    abs_path = os.path.join(BASE_DIR, req_path)
    if req_path == '':
        return redirect('/'+session['repo'])
    # Return 404 if path doesn't exist
    if not os.path.exists(abs_path):
        return abort(404)

    # Check if path is a file and serve
    if os.path.isfile(abs_path):
        return send_file(abs_path)

    # Show directory contents
    files = sorted(os.listdir(abs_path),reverse=True)
    return render_template_string('''<ul>
    {% for file in files %}
    <li><a href="{%if req_path %}/{{req_path}}{% endif%}/{{ file }}">{{ file }}</a></li>
    {% endfor %}
</ul>''', files=files,req_path=req_path)    
@app.route("/log",methods=['GET'])
@auth.login_required
def showlog():
    return open(session['repo'] + '.log','r').read(), 200, {'Content-Type': 'text/plain; charset=utf-8'}
    

@app.route("/webhooklog",methods=['GET'])
def showwebhooklog():
    return open('/var/webhook/webhook.log','r').read(), 200, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route("/addrepo",methods=['GET'])
def addrepo():
    repos = json.loads(open('repos.json','rb').read())
    repo = request.args.get('repo',"")
    key = request.args.get('key',"")
    url = request.args.get('url',"")
    password = request.args.get('pass',"")
    if key != md5.md5(repo + app.config['SECRET_KEY']*20 + repo).hexdigest():
        abort(403)
    if repo in repos:
        abort(403)
    repos[repo] = {
        "url": url,
        "pass":md5.md5(password+app.config['SECRET_KEY']).hexdigest()
    }
    open('repos.json','wb').write(json.dumps(repos))
    return "OK"






if __name__ == "__main__":
    webhooklog.info('started web server...')
    app.run(host='0.0.0.0', port = 8000,threaded=True)
