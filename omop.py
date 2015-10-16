# -*- coding: utf-8 -*-
# -*- mode: python; -*-
"""exec" "`dirname \"$0\"`/call.sh" "$0" "$@";" """
from __future__ import print_function

import os
import sys
import json
import sqlalchemy

gender_label = {
    "M": "primary",
    "W": "danger",
    "F": "danger"
}
gender_map = {
    "M": "M",
    "W": "F",
    "F": "F"
}

color_map = {
    "Condition": "#4daf4a",
    "Procedure": "#ff7f00"
}

from StringIO import StringIO

import util

class OMOP():
    def __init__(self, settings):
        username = settings['omop_user']
        password = settings['omop_passwd']
        host = settings['omop_host']
        port = settings['omop_port']
        database = settings['omop_db']
        self.schema = settings['omop_schema']
        self.db = sqlalchemy.create_engine('postgresql://{0}:{1}@{2}:{3}/{4}'.format(username, password, host, port, database))

    def _exec(self, query, **args):
        connection = None
        try:
            connection = self.db.connect()
            q = query.format(schema=self.schema)
            # DEBUG!
            qq = q
            for k in args.keys():
                qq = qq.replace(':'+str(k), "'" + str(args[k]) + "'")
            qq = qq + ';'
            print("{0}".format(qq))
            return connection.execute(sqlalchemy.text(q), **args)
        finally:
            if connection is not None:
                connection.close()

    def _exec_one(self, query, **args):
        result = self._exec(query, **args)
        res = None
        for r in result:
            if res is not None:
                raise ValueError("expected one result row got more\n{0}\n".format(query))
            res = r
        if res is None:
            raise ValueError("expected one result row got 0\n{0}\n".format(query))
        return res

    def list_patients(self, patients, prefix="", limit=None, show_old_ids=False):
        limit_str = " LIMIT :limit" if limit is not None else ""
        query = "SELECT person_id, person_source_value FROM {schema}.person{limit}".format(schema=self.schema, limit=limit_str)
        for r in self._exec(query, limit=limit):
            patients.add(str(prefix) + (str(r['person_id']) if not show_old_ids else str(r['person_source_value']) + '.json'))

    def get_person_id(self, pid):
        query = "SELECT person_id FROM {schema}.person WHERE person_source_value = :pid"
        return str(self._exec_one(query, pid=pid)['person_id'])

    def add_info(self, obj, id, key, value, has_label = False, label = ""):
        for info in obj["info"]:
            if info["id"] == id:
                if str(value) != str(info["value"]):
                    print('duplicate "'+id+'" new: '+str(value)+' old: '+str(info["value"]), file=sys.stderr)
                return
        node = {
            "id": id,
            "name": key,
            "value": value,
        }
        if has_label:
            node["label"] = label
        obj["info"].append(node)

    def get_info(self, pid, obj):
        query = "SELECT year_of_birth, gender_source_value FROM {schema}.person WHERE person_id = :pid"
        result = self._exec_one(query, pid=str(pid))
        self.add_info(obj, 'born', 'Born', int(result['year_of_birth']))
        gender = str(result['gender_source_value'])
        self.add_info(obj, 'gender', 'Gender', gender_map.get(gender, 'U'), True, gender_label.get(gender, "default"))

    def to_time(self, value):
        return util.toTime(value.strftime("%Y%m%d"))

    def create_event(self, group, id, claim_id, has_result=False, result_flag=False, result=""):
        res = {
            "id": id,
            "group": group
        }
        if claim_id is not None:
            res["row_id"] = claim_id
        if has_result:
            res["flag_value"] = result
            res["flag"] = result_flag
        return res

    def add_dict(self, dict, group, prefix, id, name, desc, unmapped):
        if group not in dict:
            dict[group] = {}
            dict[group][""] = {
                "id": "",
                "name": group,
                "desc": group,
                "color": color_map.get(group, "lightgray"),
                "parent": ""
            }
        g = dict[group]
        full_id = str(prefix) + str(id)
        if full_id not in g:
            res = {
                "id": id,
                "name": name,
                "desc": desc,
                "parent": ""
            }
            if unmapped:
                res["unmapped"] = True
            g[full_id] = res

    def get_diagnoses(self, pid, obj, dict):
        query = """SELECT
            o.condition_occurrence_id as id_row,
            o.condition_start_date as date_start,
            o.condition_end_date as date_end,
            o.condition_concept_id as d_id,
            o.condition_source_value as d_orig,
            c.domain_id as d_domain,
            c.concept_name as d_name,
            c.vocabulary_id as d_vocab,
            c.concept_code as d_num
           FROM
            {schema}.condition_occurrence as o,
            {schema}.concept as c
           WHERE
            o.person_id = :pid
            and c.concept_id = o.condition_concept_id
        """
        for row in self._exec(query, pid=pid):
            code = row['d_num']
            unmapped = False
            if code == 0:
                code = row['d_orig']
                unmapped = True
            id_row = 'c' + str(row['id_row'])
            d_id = row['d_id']
            name = row['d_name']
            vocab = row['d_vocab']
            group = row['d_domain']
            desc = "{0} ({1} {2})".format(name, vocab, code)
            self.add_dict(dict, group, vocab, d_id, name, desc, unmapped)
            date_start = self.to_time(row['date_start'])
            date_end = self.to_time(row['date_end']) if row['date_end'] else date_start
            date_cur = date_start
            while date_cur <= date_end:
                event = self.create_event(group, str(vocab) + str(d_id), id_row)
                event['time'] = date_cur
                obj['events'].append(event)
                date_cur = util.nextDay(date_cur)

    def get_procedures(self, pid, obj, dict):
        query = """SELECT
            o.procedure_occurrence_id as id_row,
            o.procedure_date as p_date,
            o.procedure_concept_id as p_id,
            o.procedure_source_value as p_orig,
            c.domain_id as p_domain,
            c.concept_name as p_name,
            c.vocabulary_id as p_vocab,
            c.concept_code as p_num,
            p.total_paid as p_cost
           FROM
            {schema}.procedure_occurrence as o,
            {schema}.concept as c
           LEFT OUTER JOIN
            {schema}.procedure_cost as p
           ON
            p.procedure_occurrence_id = o.procedure_occurrence_id
           WHERE
            o.person_id = :pid
            and c.concept_id = o.procedure_concept_id
        """
        for row in self._exec(query, pid=pid):
            code = row['p_num']
            unmapped = False
            if code == 0:
                code = row['p_orig']
                unmapped = True
            id_row = 'p' + str(row['id_row'])
            d_id = row['p_id']
            name = row['p_name']
            vocab = row['p_vocab']
            group = row['p_domain']
            desc = "{0} ({1} {2})".format(name, vocab, code)
            self.add_dict(dict, group, vocab, d_id, name, desc, unmapped)
            event = self.create_event(group, str(vocab) + str(d_id), id_row)
            event['time'] = self.to_time(row['p_date'])
            if row['p_cost']:
                event['cost'] = row['p_cost']
            obj['events'].append(event)

    def get_drugs(self, pid, obj, dict):
        query = """SELECT
            o.drug_exposure_id as id_row,
            o.drug_exposure_start_date as date_start,
            o.drug_exposure_end_date as date_end,
            o.drug_type_concept_id as m_id,
            o.drug_source_value as m_orig,
            c.domain_id as m_domain,
            c.concept_name as m_name,
            c.vocabulary_id as m_vocab,
            c.concept_code as m_num,
            p.total_paid as m_cost
           FROM
            {schema}.drug_exposure as o,
            {schema}.concept as c
           LEFT OUTER JOIN
            {schema}.drug_cost as p
           ON
            o.drug_exposure_id = p.drug_exposure_id
           WHERE
            o.person_id = :pid
            and c.concept_id = o.drug_type_concept_id
        """
        for row in self._exec(query, pid=pid):
            code = row['m_num']
            unmapped = False
            if code == 0:
                code = row['m_orig']
                unmapped = True
            id_row = 'm' + str(row['id_row'])
            d_id = row['m_id']
            name = row['m_name']
            vocab = row['m_vocab']
            group = row['m_domain']
            desc = "{0} ({1} {2})".format(name, vocab, code)
            self.add_dict(dict, group, vocab, d_id, name, desc, unmapped)
            date_start = self.to_time(row['date_start'])
            date_end = self.to_time(row['date_end']) if row['date_end'] else date_start
            date_cur = date_start
            while date_cur <= date_end:
                event = self.create_event(group, str(vocab) + str(d_id), id_row)
                event['time'] = date_cur
                obj['events'].append(event)
                if row['p_cost']:
                    event['cost'] = row['p_cost']
                    row['p_cost'] = None
                date_cur = util.nextDay(date_cur)

    def get_patient(self, pid, dictionary, line_file, class_file):
        obj = {
            "info": [],
            "events": [],
            "h_bars": [],
            "v_bars": [ "auto" ],
            "v_spans": [],
            "classes": {}
        }
        util.add_files(obj, line_file, class_file)
        self.get_info(pid, obj)
        self.add_info(obj, "pid", "Patient", pid)
        self.get_info(pid, obj)
        self.get_diagnoses(pid, obj, dictionary)
        self.get_procedures(pid, obj, dictionary)
        self.get_drugs(pid, obj, dictionary)
        min_time = float('inf')
        max_time = float('-inf')
        for e in obj["events"]:
            time = e["time"]
            if time < min_time:
                min_time = time
            if time > max_time:
                max_time = time
        obj["start"] = min_time
        obj["end"] = max_time
        self.add_info(obj, "event_count", "Events", len(obj["events"]))
        return obj