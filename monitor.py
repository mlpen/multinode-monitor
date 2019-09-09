import os
import subprocess
import time
import curses
import paramiko
import sys

username = ''
assert username != None

hosts = {'192.168.0.107:22':None, '192.168.0.109:22':None}

screen_width = 140
last_height = 0

for host in hosts:
    addr, port = host.split(':')
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(addr, port, username)
    hosts[host] = ssh

def run_command(cmd, host = None):
    if host != None:
        stdin, stdout, stderr = host.exec_command(cmd)
        return ''.join(stdout.readlines()).split('\n')[:-1]
    else:
        cmd_ = cmd.split(' ')
        p = subprocess.Popen(cmd_, 
                             stdin = subprocess.PIPE, 
                             stdout = subprocess.PIPE, 
                             stderr = subprocess.PIPE)
        output, err = p.communicate()
        rc = p.returncode
        if rc == 0:
            return output.decode('utf-8').split('\n')[:-1]
        else:
            raise RuntimeError("Run command failed: " + cmd)

def memory(host = None):
    def m2g(inp):
        return '%.02fGiB' % (float(inp) / 1024)
    
    output = run_command('free -m', host)
    assert len(output) == 3
    
    stat = {}
    
    mem = output[1].split()
    assert mem[0] == 'Mem:'
    stat['RAM'] = '%s/%s' % (m2g(mem[2]), m2g(mem[1]))
    
    swap = output[2].split()
    assert swap[0] == 'Swap:'
    stat['Swap'] = '%s/%s' % (m2g(swap[2]), m2g(swap[1]))
    
    return stat

def cpu(host = None):
    def locate(output_lines, start):
        for line_idx in range(len(output_lines)):
            if output_lines[line_idx].strip().startswith(start):
                return line_idx
        raise RuntimeError("Cannot find line starts with: " + start)
        
    def extract_cpu_stat(line):
        stat = line.replace(',', '').split()
        return {'User':'%s%%' % (stat[1]), 'Kernel':'%s%%' % (stat[3]), 
                'IO':'%s%%' % (stat[9]), 'Idle':'%s%%' % (stat[7])}
    
    def extract_proc_stat(line):
        stat = line.split()
        return {'PID':stat[0], 'User':stat[1], 
                'CPU':'%s%%' % (stat[8]), 'Memory':'%s%%' % (stat[9]), 
                'Time':stat[10], 'Command':stat[11]}
    
    output = run_command('top -b -n 1', host)
    cpu_stat = extract_cpu_stat(output[locate(output, '%Cpu(s)')])
    
    proc_stat = []
    pid_idx_start = locate(output, 'PID') + 1
    for line in output[pid_idx_start:-1]:
        proc_stat.append(extract_proc_stat(line))
        
    return {'CPU':cpu_stat, 'Process':proc_stat}
    
def gpu(host = None):
    query = ['index',
             'pci.bus_id',
             'name',
             'driver_version',
             'pstate',
             'pcie.link.gen.max',
             'pcie.link.gen.current',
             'pcie.link.width.max',
             'pcie.link.width.current',
             'power.limit',
             'power.draw',
             'temperature.gpu',
             'fan.speed',
             'utilization.gpu',
             'utilization.memory',
             'memory.total',
             'memory.used']
    output = run_command('nvidia-smi --query-gpu=%s --format=csv,noheader' % (','.join(query)), host)
    gpu_stat = [None] * len(output)
    for line in output:
        values = line.split(', ')
        assert len(values) == len(query)
        info = {query[idx]:values[idx] for idx in range(len(values))}
        index = int(info['index'])
        gpu_stat[index] = {'Idx':info['index'],
                           'PCIe Bus':info['pci.bus_id'],
                           'GPU':info['name'],
                           'Driver Version':info['driver_version'],
                           'Perf':info['pstate'],
                           'PCIeGen':'%s/%s' % (info['pcie.link.gen.current'], info['pcie.link.gen.max']),
                           'PCIeLink':'%s/%s' % (info['pcie.link.width.current'], info['pcie.link.width.max']),
                           'Power':'%s/%s' % (info['power.draw'].replace(' ', ''), info['power.limit'].replace(' ', '')),
                           'Temp':'%sC' % (info['temperature.gpu']),
                           'Fan':info['fan.speed'].replace(' ', ''),
                           'GPU Util':info['utilization.gpu'].replace(' ', ''),
                           'Mem Util':info['utilization.memory'].replace(' ', ''),
                           'Memory':'%s/%s' % (info['memory.used'].replace(' ', ''), info['memory.total'].replace(' ', '')),
                           'Process':[]}
    
    query = ['gpu_bus_id', 'pid', 'process_name' , 'used_memory']
    output = run_command('nvidia-smi --query-compute-apps=%s --format=csv,noheader' % (','.join(query)), host)
    for line in output:
        values = line.split(', ')
        assert len(values) == len(query)
        info = {query[idx]:values[idx] for idx in range(len(values))}
        proc = {'PID':info['pid'], 'Memory Usage':info['used_memory'], 'Command':info['process_name']}
        for stat in gpu_stat:
            if stat['PCIe Bus'] == info['gpu_bus_id']:
                stat['Process'].append(proc)
    
    return gpu_stat

def get_all_stat(host = None):
    stat = {'Date':run_command('date', ssh)[0]}
    stat['Memory'] = memory(host)
    stat.update(cpu(host))
    stat['NVIDIA'] = gpu(host)
    return stat

def print_stat(stat):
    def fs(string, parameter):
        if type(parameter) == tuple:
            width = parameter[0]
            left_align = parameter[1]
        elif type(parameter) == int:
            width = parameter
            left_align = True
        
        assert width >= 2
        if len(string) > width:
            string = string[:width - 2] + '..'
        if left_align:
            return ('{0: <%d}' % (width)).format(string)
        else:
            return ('{0: >%d}' % (width)).format(string)
    
    def info_to_str(info, fs = None, seperator = '   '):
        pairs = []
        for key in info:
            pair = (key, info[key])
            if fs == None:
                pairs.append('%s: %s' % pair)
            else:
                pairs.append(fs[0]('%s: %s' % pair, fs[1][key]))
        return seperator.join(pairs)
    
    def print_procs(procs, output, limit = 10):
        keys = list(procs[0].keys())
        count = 0
        ps = []
        for proc in stat['Process']:
            width = 12
            lines = [fs(proc[key], width) for key in keys[:-1]]
            cmd = proc[keys[-1]]
            if len(cmd) > width:
                lines.append(cmd[:width])
                lines.append(fs(cmd[width:], width))
            else:
                lines.append(fs(cmd, width))
                lines.append(fs('', width))
            
                
            ps.append(lines)
            count += 1
            if count >= limit:
                break

        for line_idx in range(len(keys) + 1):
            if line_idx < len(keys):
                header = keys[line_idx]
            else:
                header = ''
            output.append('|'.join([fs(header, 8)] + [p[line_idx] for p in ps]))
    
    def print_gpu_stat(gpu_stat, output):
        output.append('-' * screen_width)
        output.append('NVIDIA    Driver Version: %s' % gpu_stat[0]['Driver Version'])
        keys = [('Idx', 3), 
                ('GPU', 19), 
                ('Fan', 4), 
                ('Temp',4), 
                ('Power', (16, False)), 
                ('Perf', 4), 
                ('PCIeLink', 8), 
                ('PCIeGen', 7), 
                ('GPU Util', 8), 
                ('Mem Util', 8), 
                ('Memory', (17, False))]
        output.append('  '.join([fs(key[0], key[1]) for key in keys]))
        output.append('=' * screen_width)
        for gpu in gpu_stat:
            output.append('  '.join([fs(gpu[key[0]], key[1]) for key in keys]))
            for proc in gpu['Process']:
                output.append(info_to_str(proc, fs = (fs, {'PID':11, 'Type':8, 'Memory Usage':23, 'Command':30})))
            output.append('-' * screen_width)
    
    output = []
    output.append('Memory -- %s' % (info_to_str(stat['Memory'])))
    output.append('CPU ----- %s' % (info_to_str(stat['CPU'])))
    output.append('-' * screen_width)
    print_procs(stat['Process'], output)
    print_gpu_stat(stat['NVIDIA'], output)
    return output

def show_print(output):
    global last_height

    for idx in range(len(output)):
        line = ('{0: <%d}' % (screen_width)).format(output[idx])
        try:
            stdscr.addstr(idx, 0, line)
        except curses.error as e:
            pass
    if last_height > len(output):
        for idx in range(len(output), last_height):
            try:
                stdscr.addstr(idx, 0, ' ' * screen_width)
            except curses.error as e:
                pass
    
    last_height = len(output)
    
    stdscr.refresh()

if True:
    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()

    try:
        while True:
            output = []
            for host in hosts:
                stat = get_all_stat(hosts[host])
                output.append('Host Machine: %s    %s    Online' % (host, stat['Date']))
                output.extend(print_stat(stat))
            show_print(output)
            time.sleep(0.5)
    except KeyboardInterrupt as e:
        print("Keyboard Interrupted")
    finally:
        for host in hosts:
            try:
                hosts[host].close()
            except Exception as e:
                print(e)
        curses.echo()
        curses.nocbreak()
        curses.endwin()
