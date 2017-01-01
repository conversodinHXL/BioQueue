#!/usb/bin/env python
from numpy import *
import numpy
from databaseDriver import con_mysql, get_resource, update_resource
from baseDriver import get_disk_free, get_cpu_available, get_memo_usage_available, get_all_config
import pandas as pd
settings = get_all_config()


def load_train_frame(step_hash):
    try:
        conn, cur = con_mysql()
        sql = """SELECT * FROM `%s` WHERE `step`='%s';"""%(settings['datasets']['train_db'], step_hash)
        train_dataframe = pd.read_sql_query(sql, conn)
        train_dataframe = train_dataframe.replace('-1', numpy.nan)
        train_dataframe['input'] = train_dataframe['input'].astype('float32')
        train_dataframe['output'] = train_dataframe['output'].astype('float32')
        train_dataframe['cpu'] = train_dataframe['cpu'].astype('float32')
        train_dataframe['mem'] = train_dataframe['mem'].astype('float32')
        train_dataframe = train_dataframe.fillna(train_dataframe.mean())
        tmp_x = list(train_dataframe['input'])
        tmp_out = list(train_dataframe['output'])
        tmp_mem = list(train_dataframe['mem'])
        tmp_cpu = list(train_dataframe['cpu'])
        all_x = [[1.0, float(feat)] for feat in tmp_x]
        out_y = [float(label) for label in tmp_out]
        mem_y = [float(label) for label in tmp_mem]
        cpu_y = [float(label) for label in tmp_cpu]
        conn.close()
        return all_x, out_y, mem_y, cpu_y
    except Exception, err:
        print err
        return 0, 0, 0, 0


def stand_regression(x_array, y_array):
    x_matrix = mat(x_array)
    y_matrix = mat(y_array).T
    x_t_x = x_matrix.T*x_matrix
    if linalg.det(x_t_x) == 0.0:
        return
    ws = x_t_x.I * (x_matrix.T*y_matrix)
    return ws


def reg_single_feature(x, y):
    rc = stand_regression(x, y)
    if rc is not None:
        rc = rc.getA()
        x_matrix = mat(x)
        y_matrix = mat(y)
        x_copy = x_matrix.copy()
        x_copy.sort(0)
        y_hat = x_copy * rc
        r = corrcoef(y_hat.T, y_matrix)[0][1]
        b = rc[0][0]
        a = rc[1][0]
        if abs(r) < float(settings['ml']['threshold']):
            a = 0
            b = numpy.mean(y)
            r = numpy.std(y)
            
        if numpy.isnan(a):
            a = numpy.float(0)
        if numpy.isnan(b):
            b = numpy.mean(y)
        if numpy.isnan(r):
            r = numpy.std(y)
        try:
            a = a.item()
        except Exception, e:
            pass
        try:
            b = b.item()
        except Exception, e:
            pass
        try:
            r = r.item()
        except Exception, e:
            pass
    else:
        a = 0
        b = numpy.mean(y)
        r = numpy.std(y)
    return a, b, r


def record_result(step_hash, a, b, r, t):
    try:
        conn, cur = con_mysql()
        sql = """INSERT INTO `%s` (`step_hash`, `a`, `b`, `r`, `type`) VALUES ('%s', '%s', '%s', '%s', '%s');"""\
              % (settings['datasets']['equation'], step_hash, a, b, r, t)
        cur.execute(sql)
        conn.commit()
        conn.close()
    except Exception, err:
        print err
        return 0
    return 1


def regression(step_hash, save=1):
    x, out, mem, cpu = load_train_frame(step_hash)
    # Output Size
    ao, bo, ro = reg_single_feature(x, out)
    ao = 0 if numpy.isnan(ao) else ao
    bo = 0 if numpy.isnan(bo) else bo
    ro = 0 if numpy.isnan(ro) else ro

    # Memory Usage
    am, bm, rm = reg_single_feature(x, mem)
    am = 0 if numpy.isnan(am) else am
    bm = 0 if numpy.isnan(bm) else bm
    rm = 0 if numpy.isnan(rm) else rm

    # CPU Usage
    ac, bc, rc = reg_single_feature(x, cpu)
    ac = 0 if numpy.isnan(ac) else ac
    bc = 0 if numpy.isnan(bc) else bc
    rc = 0 if numpy.isnan(rc) else rc

    if save:
        record_result(step_hash, ao, bo, ro, 1)
        record_result(step_hash, am, bm, rm, 2)
        record_result(step_hash, ac, bc, rc, 3)
    
    return ao, bo, am, bm, ac, bc


def get_training_items(step_hash):
    dyn_sql = """SELECT COUNT(*) FROM %s WHERE `step`='%s';""" % (settings['datasets']['train_db'], step_hash)
    try:
        conn, cur = con_mysql()
        cur.execute(dyn_sql)
        trains = cur.fetchone()
        conn.commit()
        conn.close()
        return trains[0]
    except Exception, err:
        print err
        return 0


def is_fifo(protocol, step_ord, job_id):
    sql = """SELECT COUNT(*) FROM `%s` WHERE `protocol_id` = %s AND `resume` < %s AND `id` < %s AND `status` != -3 AND `status` != -1;""" \
            % (settings['datasets']['job_db'], protocol, step_ord+1, job_id)
    print sql
    try:
        con, cursor = con_mysql()
        cursor.execute(sql)
        job_left_behind = cursor.fetchone()
        con.commit()
        con.close()
        return job_left_behind[0]
    except Exception, e:
        print e
        return 0


def check_ok_to_go(job_id, step, protocol_id, step_ord, in_size=-99999.0, training_num=0, run_path='/'):
    if is_fifo(protocol_id, step_ord, job_id) > 0:
        return 0, 0, 0, 0
    try:
        conn, cur = con_mysql()
        get_equation_sql = """SELECT `a`, `b`, `type` FROM %s WHERE `step_hash`='%s';""" \
                         % (settings['datasets']['equation'], str(step))
        cur.execute(get_equation_sql)
        equations = cur.fetchall()
        if len(equations) > 0 and in_size != -99999.0:
            predict_need = {}
            cpu_max_pool, memory_max_pool, disk_max_pool = get_resource()
            cpu_max_pool = float(cpu_max_pool)
            memory_max_pool = float(memory_max_pool)
            disk_max_pool = float(disk_max_pool)
            for equation in equations:
                a = float(equation[0])
                b = float(equation[1])
                t = equation[2]
                needed = (a * in_size + b)*float(settings['ml']['confidence_weight'])
                if t == 1:
                    predict_need['disk'] = needed
                    if needed > get_disk_free(run_path) or needed > disk_max_pool:
                        conn.close()
                        return 0, 0, 0, 0
                elif t == 2:
                    predict_need['mem'] = needed
                    if needed > get_memo_usage_available() or needed > memory_max_pool:
                        conn.close()
                        return 0, 0, 0, 0
                elif t == 3:
                    predict_need['cpu'] = needed
                    if needed > get_cpu_available() or needed > cpu_max_pool:
                        conn.close()
                        return 0, 0, 0, 0
            print '=='+str(job_id)+'=='+str(step)+'==', 'cpu: pred', predict_need['cpu'], 'get_cpu', get_cpu_available(), 'cpuPool', cpu_max_pool, 'mem: pred', predict_need['mem'], 'get_mem', get_memo_usage_available(), 'memPool', memory_max_pool, 'disk: pred', predict_need['disk'], 'getDisk', get_disk_free(run_path), 'diskPool', disk_max_pool            
            if update_resource(-1*predict_need['cpu'], -1*predict_need['mem'], -1*predict_need['disk']):
                conn.close()
                return 1, predict_need['cpu'], predict_need['mem'], predict_need['disk']
            else:
                print '=='+str(job_id)+'=='+str(step)+'==recheck reject=='
                conn.close()
                return 0, predict_need['cpu'], predict_need['mem'], predict_need['disk']
        else:
            # training_num = get_training_items(conn, cur, step)
            if training_num < 3:
                # Not ready for machine learning
                get_running_sql = """SELECT COUNT(*) FROM %s WHERE `status`>0 AND `id` != %s;""" %\
                                (settings['datasets']['job_db'], job_id)
                cur.execute(get_running_sql)
                running = cur.fetchone()
                conn.close()
                if running:
                    if running[0] == 0:
                        return 1, 0, 0, 0
                    else:
                        return 0, 0, 0, 0
                else:
                    return 1, 0, 0, 0
            else:
                cpu_max_pool, memory_max_pool, disk_max_pool = get_resource()
                cpu_max_pool = float(cpu_max_pool)
                memory_max_pool = float(memory_max_pool)
                disk_max_pool = float(disk_max_pool)
                if training_num < 10:
                    ao, bo, am, bm, ac, bc = regression(step, 0)
                else:
                    ao, bo, am, bm, ac, bc = regression(step)
                disk_needed = int((ao*in_size+bo)*float(settings['ml']['confidence_weight']))
                memory_needed = int((am*in_size+bm)*float(settings['ml']['confidence_weight']))
                cpu_needed = int((ac*in_size+bc)*float(settings['ml']['confidence_weight']))
                print '=='+str(job_id)+'=='+str(step)+'==', 'cpu: pred', cpu_needed, 'get_cpu', get_cpu_available(), 'cpuPool', cpu_max_pool, 'mem: pred', memory_needed, 'get_mem', get_memo_usage_available(), 'memPool', memory_max_pool, 'disk: pred', disk_needed, 'getDisk', get_disk_free(run_path), 'diskPool', disk_max_pool
                conn.close()
                if disk_needed > get_disk_free(run_path) or disk_needed > disk_max_pool:
                    return 0, cpu_needed, memory_needed, disk_needed
                if memory_needed > get_memo_usage_available() or memory_needed > memory_max_pool:
                    return 0, cpu_needed, memory_needed, disk_needed
                if cpu_needed > get_cpu_available() or cpu_needed > cpu_max_pool:
                    return 0, cpu_needed, memory_needed, disk_needed
                
                if update_resource(-1*cpu_needed, -1*memory_needed, -1*disk_needed):
                    return 1, cpu_needed, memory_needed, disk_needed
                else:
                    return 0, cpu_needed, memory_needed, disk_needed
    except Exception, err:
        print err
        return 0, 0, 0, 0
