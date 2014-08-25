#!/usr/bin/env python
"""

owtf is an OWASP+PTES-focused try to unite great tools and facilitate pen testing
Copyright (c) 2011, Abraham Aranguren <name.surname@gmail.com> Twitter: @7a_ http://7-a.org
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the copyright owner nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

The DB stores HTTP transactions, unique URLs and more.

"""

import os
import re
import json
import base64
import logging

from sqlalchemy import desc, asc
from collections import defaultdict

from framework.lib.exceptions import InvalidTransactionReference, \
                                     InvalidParameterType
from framework.http import transaction
from framework.db import models


REGEX_TYPES = ['HEADERS', 'BODY']  # The regex find differs for these types :P


class TransactionManager(object):
    def __init__(self, Core):
        self.Core = Core  # Need access to reporter for pretty html trasaction log
        self.regexs = defaultdict(list)
        for regex_type in REGEX_TYPES:
            self.regexs[regex_type] = {}
        self.CompileRegexs()

    def NumTransactions(self, Scope = True, target_id = None):  # Return num transactions in scope by default
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        count = session.query(models.Transaction).filter_by(scope = Scope).count()
        session.close()
        return(count)

    def IsTransactionAlreadyAdded(self, Criteria, target_id = None):
        return(len(self.GetAll(Criteria, target_id)) > 0)

    def GenerateQueryUsingSession(self, session, criteria, for_stats=False):
        query = session.query(models.Transaction)
        # If transaction search is being done
        if criteria.get('search', None):
            if criteria.get('url', None):
                if isinstance(criteria.get('url'), list):
                    criteria['url'] = criteria['url'][0]
                query = query.filter(models.Transaction.url.like(
                    '%'+criteria['url']+'%'))
            if criteria.get('method', None):
                if isinstance(criteria.get('method'), list):
                    criteria['method'] = criteria['method'][0]
                query = query.filter(models.Transaction.method.like(
                    '%'+criteria.get('method')+'%'))
            if criteria.get('data', None):
                if isinstance(criteria.get('data'), list):
                    criteria['data'] = criteria['data'][0]
                query = query.filter(models.Transaction.data.like(
                    '%'+criteria.get('data')+'%'))
            if criteria.get('raw_request', None):
                if isinstance(criteria.get('raw_request'), list):
                    criteria['raw_request'] = criteria['raw_request'][0]
                query = query.filter(models.Transaction.raw_request.like(
                    '%'+criteria.get('raw_request')+'%'))
            if criteria.get('response_status', None):
                if isinstance(criteria.get('response_status'), list):
                    criteria['response_status'] = criteria['response_status'][0]
                query = query.filter(models.Transaction.response_status.like(
                    '%'+criteria.get('response_status')+'%'))
            if criteria.get('response_headers', None):
                if isinstance(criteria.get('response_headers'), list):
                    criteria['response_headers'] = criteria['response_headers'][0]
                query = query.filter(models.Transaction.response_headers.like(
                    '%'+criteria.get('response_headers')+'%'))
            if criteria.get('response_body', None):
                if isinstance(criteria.get('response_body'), list):
                    criteria['response_body'] = criteria['response_body'][0]
                query = query.filter(
                    models.Transaction.binary_response==False,
                    models.Transaction.response_body.like(
                        '%'+criteria.get('response_body')+'%'))
        else:  # If transaction filter is being done
            if criteria.get('url', None):
                if isinstance(criteria.get('url'), (str, unicode)):
                    query = query.filter_by(url = criteria['url'])
                if isinstance(criteria.get('url'), list):
                    query = query.filter(models.Transaction.url.in_(criteria.get('url')))
            if criteria.get('method', None):
                if isinstance(criteria.get('method'), (str, unicode)):
                    query = query.filter_by(method = criteria['method'])
                if isinstance(criteria.get('method'), list):
                    query = query.filter(models.Transaction.method.in_(criteria.get('method')))
            if criteria.get('data', None):
                if isinstance(criteria.get('data'), (str, unicode)):
                    query = query.filter_by(data = criteria['data'])
                if isinstance(criteria.get('data'), list):
                    query = query.filter(models.Transaction.data.in_(criteria.get('data')))
        # For the following section doesn't matter if filter/search because
        # it doesn't make sense to search in a boolean column :P
        if criteria.get('scope', None):
            if isinstance(criteria.get('scope'), list):
                criteria['scope'] = criteria['scope'][0]
            query = query.filter_by(scope = self.Core.Config.ConvertStrToBool(criteria['scope']))
        if criteria.get('binary_response', None):
            if isinstance(criteria.get('binary_response'), list):
                criteria['binary_response'] = criteria['binary_response'][0]
            query = query.filter_by(binary_response = self.Core.Config.ConvertStrToBool(criteria['binary_response']))
        if not for_stats:  # query for stats shouldn't have limit and offset
            try:
                if criteria.get('offset', None):
                    if isinstance(criteria.get('offset'), list):
                        criteria['offset'] = criteria['offset'][0]
                    query = query.offset(int(criteria['offset']))
                if criteria.get('limit', None):
                    if isinstance(criteria.get('limit'), list):
                        criteria['limit'] = criteria['limit'][0]
                    query = query.limit(int(criteria['limit']))
            except ValueError:
                raise InvalidParameterType("Invalid parameter type for transaction db")
        return(query)

    def GetFirst(self, Criteria, target_id = None): # Assemble only the first transaction that matches the criteria from DB
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        query = self.GenerateQueryUsingSession(session, Criteria)
        return(self.DeriveTransaction(query.first()))

    def GetAll(self, Criteria, target_id = None): # Assemble ALL transactions that match the criteria from DB
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        query = self.GenerateQueryUsingSession(session, Criteria)
        return(self.DeriveTransactions(query.all()))

    def DeriveTransaction(self, t):
        if t:
            owtf_transaction = transaction.HTTP_Transaction(None)
            response_body = t.response_body
            if t.binary_response:
                response_body = base64.b64decode(response_body)
            owtf_transaction.SetTransactionFromDB(
                                                    t.id,
                                                    t.url,
                                                    t.method,
                                                    t.response_status,
                                                    str(t.time),
                                                    t.time_human,
                                                    t.data,
                                                    t.raw_request,
                                                    t.response_headers,
                                                    response_body
                                                 )
            return owtf_transaction
        return(None)

    def DeriveTransactions(self, transactions):
        owtf_tlist = []
        for transaction in transactions:
            owtf_tlist.append(self.DeriveTransaction(transaction))
        return(owtf_tlist)

    def GetTransactionModel(self, transaction):
        try:
            response_body = unicode(transaction.GetRawResponseBody(), "utf-8")
            binary_response = False
        except UnicodeDecodeError:
            response_body = base64.b64encode(transaction.GetRawResponseBody())
            binary_response = True
        finally:
            transaction_model = models.Transaction(
                url=transaction.URL,
                scope=transaction.InScope(),
                method=transaction.Method,
                data=transaction.Data,
                time=float(transaction.Time),
                time_human=transaction.TimeHuman,
                raw_request=transaction.GetRawRequest(),
                response_status=transaction.GetStatus(),
                response_headers=transaction.GetResponseHeaders(),
                response_body=response_body,
                binary_response=binary_response,
                session_tokens=transaction.GetSessionTokens(),
                login=None,
                logout=None
            )
            return transaction_model

    def LogTransactions(self, transaction_list, target_id=None):
        """
        This function does the following things in order
        + Add all transactions to a session and commit
        + Add all the grepped results and commit
        + Add all urls to url db
        """
        # Create a usable session
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        # Initiate urls_list for holding urls and transaction_model_list
        # for holding transaction models
        urls_list = []
        transaction_model_list = []
        # Add transactions and commit so that we can have access to
        # transaction ids etc..
        for transaction in transaction_list:
            # TODO: This shit will go crazy on non-ascii characters
            transaction_model = self.GetTransactionModel(transaction)
            transaction_model_list.append(transaction_model)
            session.add(transaction_model)
            urls_list.append([transaction.URL, True, transaction.InScope()])
        session.commit()
        # Now since we have the ids ready, we can process the grep output and
        # add accordingly. So iterate over transactions and their models.
        for i in range(0, len(transaction_list)):
            # Get the transaction and transaction model from their lists
            owtf_transaction = transaction_list[i]
            transaction_model = transaction_model_list[i]
            # Check if grepping is valid for this transaction
            # For grepping to be valid
            # + Transaction must not have a binary response
            # + Transaction must be in scope
            if (not transaction_model.binary_response) and (transaction_model.scope):
                # Get the grep results
                grep_outputs = self.GrepTransaction(owtf_transaction)
                if grep_outputs:  # If valid grep results exist
                    # Iterate over regex_name and regex results
                    for regex_name, regex_results in grep_outputs.iteritems():
                        # Then iterate over the results to store each result in
                        # a seperate row, but also check to avoid duplicate
                        # entries as we have many-to-many relationship
                        # available for linking
                        for match in regex_results:
                            # Conver the match to json
                            match = json.dumps(match)
                            # Fetch if any existing entry
                            existing_grep_output = session.query(
                                models.GrepOutput).filter_by(
                                    name=regex_name,
                                    output=match).first()
                            if existing_grep_output:
                                existing_grep_output.transactions.append(
                                    transaction_model)
                                session.merge(existing_grep_output)
                            else:
                                session.add(models.GrepOutput(
                                    transactions=[transaction_model],
                                    name=regex_name,
                                    output=match))
        session.commit()
        zest_trans_list = []
        # Append the transaction in the list if recording is set to on
        if self.Core.zest.IsRecording():
            for model in transaction_model_list:
                zest_trans_list.append((target_id, model.id))
            self.Core.zest.addtoRecordedTrans(zest_trans_list)
        session.close()
        self.Core.DB.URL.ImportProcessedURLs(urls_list, target_id)

    def LogTransactionsFromLogger(self, transactions_dict):
        # transaction_dict is a dictionary with target_id as key and list of owtf transactions
        for target_id, transaction_list in transactions_dict.items():
            if transaction_list:
                self.LogTransactions(transaction_list, target_id)

    def DeleteTransaction(self, transaction_id, target_id = None):
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        session.delete(session.query(models.Transaction).get(transaction_id))
        session.commit()
        session.close()

    def GetNumTransactionsInScope(self, target_id = None):
        return self.NumTransactions(target_id = target_id)

    def GetByID(self, ID, target_id = None):
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        model_obj = session.query(models.Transaction).get(ID)
        session.close()
        if model_obj:
            return(self.DeriveTransaction(model_obj))
        return(model_obj) # None returned if no such transaction

    def GetByIDs(self, id_list, target_id = None):
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        model_objs = []
        for ID in id_list:
            model_obj = session.query(models.Transaction).get(ID)
            if model_obj:
                model_objs.append(model_obj)
        session.close()
        return(self.DeriveTransactions(model_objs))

    def GetTopTransactionsBySpeed(self, Order = "Desc", Num = 10):
        Session = self.Core.DB.Target.GetTransactionDBSession()
        session = Session()
        if Order == "Desc":
            results = session.query(models.Transaction).order_by(desc(models.Transaction.time)).limit(Num)
        else:
            results = session.query(models.Transaction).order_by(asc(models.Transaction.time)).limit(Num)
        session.close()
        return(self.DeriveTransactions(results))

    def CompileHeaderRegex(self, header_list):
        return(re.compile('('+'|'.join(header_list)+'): ([^\r]*)', re.IGNORECASE))

    def CompileResponseRegex(self, regexp):
        return(re.compile(regexp, re.IGNORECASE | re.DOTALL))

    def CompileRegexs(self):
        for key in self.Core.Config.GetFrameworkConfigDict().keys():
            key = key[3:-3] # Remove "@@@"
            if key.startswith('HEADERS'):
                header_list = self.Core.Config.GetHeaderList(key)
                self.regexs['HEADERS'][key] = self.CompileHeaderRegex(header_list)
            elif key.startswith('RESPONSE'):
                RegexpName, GrepRegexp, PythonRegexp = self.Core.Config.FrameworkConfigGet(key).split('_____')
                self.regexs['BODY'][key] = self.CompileResponseRegex(PythonRegexp)

    def GrepTransaction(self, owtf_transaction):
        grep_output = {}
        for regex_name, regex in self.regexs['HEADERS'].items():
            grep_output.update(self.GrepResponseHeaders(regex_name, regex, owtf_transaction))
        for regex_name, regex in self.regexs['BODY'].items():
            grep_output.update(self.GrepResponseBody(regex_name, regex, owtf_transaction))
        return(grep_output)

    def GrepResponseBody(self, regex_name, regex, owtf_transaction):
        return(self.Grep(regex_name, regex, owtf_transaction.GetRawResponseBody()))

    def GrepResponseHeaders(self, regex_name, regex, owtf_transaction):
        return(self.Grep(regex_name, regex, owtf_transaction.GetResponseHeaders()))

    def Grep(self, regex_name, regex, data):
        results = regex.findall(data)
        output = {}
        if results:
            output.update({regex_name: results})
        return(output)

    def SearchByRegexName(self, regex_name, stats=False, session=None, target=None):
        """
        Allows searching of the grep_outputs table using a regex name
        What this function returns :
        + regex_name
        + grep_outputs - list of unique matches
        + transaction_ids - list of one transaction id per unique match
        + match_percent
        """
        if session:
            session_provided = True
        else:
            session_provided = False
            Session = self.Core.DB.Target.GetTransactionDBSession(target)
            session = Session()
        # Get the grep outputs and only unique values
        grep_outputs = session.query(
            models.GrepOutput.output).filter_by(
                name=regex_name).group_by(models.GrepOutput.output).all()
        grep_outputs = [i[0] for i in grep_outputs]
        # Get one transaction per match
        transaction_ids = []
        for grep_output in grep_outputs:
            transaction_ids.append(session.query(models.Transaction.id).join(
                models.Transaction.grep_outputs).filter(
                    models.GrepOutput.output == grep_output).limit(1).all()[0][0])
        # Calculate stats if needed
        if stats:
            # Calculate the total number of matches
            num_matched_transactions = session.query(models.Transaction).join(
                models.Transaction.grep_outputs).filter(
                    models.GrepOutput.name == regex_name).group_by(
                        models.Transaction).count()
            # Calculate total number of transactions in scope
            num_transactions_in_scope = session.query(models.Transaction).filter_by(
                scope=True).count()
            # Calculate matched percentage
            if int(num_transactions_in_scope):
                match_percent = int((num_matched_transactions/float(num_transactions_in_scope))*100)
            else:
                match_percent = 0
        else:
            match_percent = None
        # Close the session only if it created inside this function
        if not session_provided:
            session.close()
        return([
            regex_name,
            [json.loads(i) for i in grep_outputs],
            transaction_ids,
            match_percent])

    def SearchByRegexNames(self, name_list, stats=False, target=None):
        """
        Allows searching of the grep_outputs table using a regex name
        What this function returns is a list of list containing
        + regex_name
        + grep_outputs - list of unique matches
        + transaction_ids - list of one transaction id per unique match
        + match_percent
        """
        results = []
        Session = self.Core.DB.Target.GetTransactionDBSession(target)
        session = Session()
        for regex_name in name_list:
            results.append(self.SearchByRegexName(
                regex_name,
                stats=stats,
                session=session,
                target=target))
        session.close()
        return(results)

#-------------------------------------------------- API Methods --------------------------------------------------
    def DeriveTransactionDict(self, tdb_obj, include_raw_data = False):
        tdict = dict(tdb_obj.__dict__)  # Create a new copy so no accidental changes
        tdict.pop("_sa_instance_state")
        if not include_raw_data:
            tdict.pop("raw_request", None)
            tdict.pop("response_headers", None)
            tdict.pop("response_body", None)
        else:
            if tdict["binary_response"]:
                tdict["response_body"] = base64.b64encode(str(tdict["response_body"]))
        return tdict

    def DeriveTransactionDicts(self, tdb_obj_list, include_raw_data = False):
        dict_list = []
        for tdb_obj in tdb_obj_list:
            dict_list.append(self.DeriveTransactionDict(tdb_obj, include_raw_data))
        return dict_list

    def SearchAll(self, Criteria, target_id=None, include_raw_data=False):
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        # Three things needed
        # + Total number of transactions
        # + Filtered transaaction dicts
        # + Filtered number of transactions
        total = session.query(models.Transaction).count()
        filtered_transaction_objs = self.GenerateQueryUsingSession(
            session,
            Criteria).all()
        filtered_number = self.GenerateQueryUsingSession(
            session,
            Criteria,
            for_stats=True).count()
        return({
            "records_total": total,
            "records_filtered": filtered_number,
            "data": self.DeriveTransactionDicts(
                filtered_transaction_objs,
                include_raw_data)
        })

    def GetAllAsDicts(self, Criteria, target_id = None, include_raw_data = False): # Assemble ALL transactions that match the criteria from DB
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        query = self.GenerateQueryUsingSession(session, Criteria)
        transaction_objs = query.all()
        session.close()
        return(self.DeriveTransactionDicts(transaction_objs, include_raw_data))

    def GetByIDAsDict(self, trans_id, target_id = None):
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        transaction_obj = session.query(models.Transaction).get(trans_id)
        session.close()
        if not transaction_obj:
            raise InvalidTransactionReference("No transaction with " + str(trans_id) + " exists for target with id " + str(target_id) if target_id else self.Core.DB.Target.GetTargetID())
        return self.DeriveTransactionDict(transaction_obj, include_raw_data = True)

    def GetSessionData(self, target_id=None):
        """
        * This will return the data from the `session_tokens` column in the form of a list,
          having no `null` values
        * A sample data: [{"attributes": {"Path": "/", "HttpOnly": true}, "name": "ASP.NET_SessionId", "value": "jx0ydsvwqtfgqcufazwigiih"},
                          {"attributes": {"Path": "/"}, "name": "amSessionId", "value": "618174515"}]
        """
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        session_data = session.query(models.Transaction.session_tokens).all()
        session.close()
        results = []
        for i in session_data:
            if i[0]:
                results.append(json.loads(i[0]))
        return(results)

    def GetSessionURLs(self, target_id):
        """
        This returns the data in the form of [(url1), (url2), etc]
        """
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        session_urls = session.query(models.Transaction.url).filter(group_by(models.Transaction.session_tokens)).getall()
        session.close()
        return session_urls

'''
    def AddLoginLogoutIndicator(self, target_id=None, trans_id):
        """ This adds a login/logout indicator to a specific transaction_id. """
        Session = self.Core.DB.Target.GetTransactionDBSession(target_id)
        session = Session()
        session.query(models.Transaction).get(trans_id).update({"login_logout": })
        session.close()
'''
