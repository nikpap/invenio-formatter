# -*- coding: utf-8 -*-
## $Id$
## Bibformt engine. Format XML Marc record using specified format.

## This file is part of CDS Invenio.
## Copyright (C) 2002, 2003, 2004, 2005 CERN.
##
## The CDSware is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 2 of the
## License, or (at your option) any later version.
##
## The CDSware is distributed in the hope that it will be useful, but
## WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
## General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with CDSware; if not, write to the Free Software Foundation, Inc.,
## 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.

import re
import sys
import os
import inspect
import traceback
import zlib

from invenio.errorlib import register_errors, get_msgs_for_code_list
from invenio.config import *
from invenio.search_engine import record_exists, get_fieldvalues, get_modification_date, get_creation_date, encode_for_xml
from invenio.bibrecord import create_record, record_get_field_instances, record_get_field_value, record_get_field_values
from invenio.dbquery import run_sql
from invenio.messages import language_list_long

from invenio import bibformat_dblayer
from invenio.bibformat_config import format_template_extension, format_output_extension, templates_path, elements_path, outputs_path, elements_import_path

__lastupdated__ = """$Date$"""

#Cache for data we have allready read and parsed
format_templates_cache = {}
format_elements_cache = {}
format_outputs_cache = {}
kb_mappings_cache = {}

cdslangs = language_list_long()

#Regular expression for finding <lang>...</lang> tag in format templates
pattern_lang = re.compile(r'''
    <lang              #<lang tag (no matter case)
    \s*                #any number of white spaces
    >                  #closing <lang> start tag
    (?P<langs>.*?)     #anything but the next group (greedy)
    (</lang\s*>)       #end tag
    ''', re.IGNORECASE | re.DOTALL | re.VERBOSE)

#Builds regular expression for finding each known language in <lang> tags
ln_pattern_text = r"<("
for lang in cdslangs:
    ln_pattern_text += lang[0] +r"|"
    
ln_pattern_text = ln_pattern_text.rstrip(r"|")
ln_pattern_text += r")>(.*?)</\1>"
    
ln_pattern =  re.compile(ln_pattern_text)

#Regular expression for finding <name> tag in format templates
pattern_format_template_name = re.compile(r'''
    <name              #<name tag (no matter case)
    \s*                #any number of white spaces
    >                  #closing <name> start tag
    (?P<name>.*?)      #name value. any char that is not end tag
    (</name\s*>)(\n)?  #end tag
    ''', re.IGNORECASE | re.DOTALL | re.VERBOSE)

#Regular expression for finding <description> tag in format templates
pattern_format_template_desc = re.compile(r'''
    <description           #<decription tag (no matter case)
    \s*                    #any number of white spaces
    >                      #closing <description> start tag
    (?P<desc>.*?)          #description value. any char that is not end tag
    </description\s*>(\n)? #end tag
    ''', re.IGNORECASE | re.DOTALL | re.VERBOSE)

#Regular expression for finding <BFE_ > tags in format templates
pattern_tag = re.compile(r'''
    <BFE_                        #every special tag starts with <BFE_ (no matter case)
    (?P<function_name>[^/\s]+)   #any char but a space or slash
    \s*                          #any number of spaces
    (?P<params>(\s*              #params here
     (?P<param>([^=\s])*)\s*     #param name: any chars that is not a white space or equality. Followed by space(s)
     =\s*                        #equality: = followed by any number of spaces
     (?P<sep>[\'"])              #one of the separators
     (?P<value>.*?)              #param value: any chars that is not a separator like previous one
     (?P=sep)                    #same separator as starting one
    )*)                          #many params
    \s*                          #any number of spaces
    (/)?>                        #end of the tag
    ''', re.IGNORECASE | re.DOTALL | re.VERBOSE)

#Regular expression for finding params inside <BFE_ > tags in format templates
pattern_function_params = re.compile('''
    (?P<param>([^=\s])*)\s*  # Param name: any chars that is not a white space or equality. Followed by space(s)
    =\s*                     # Equality: = followed by any number of spaces
    (?P<sep>[\'"])           # One of the separators
    (?P<value>.*?)           # Param value: any chars that is not a separator like previous one
    (?P=sep)                 # Same separator as starting one
    ''', re.VERBOSE | re.DOTALL )

#Regular expression for finding format elements "params" attributes (defined by @param)
pattern_format_element_params = re.compile('''
    @param\s*                          # Begins with @param keyword followed by space(s)
    (?P<name>[^\s=]*)\s*               # A single keyword, and then space(s)
    #(=\s*(?P<sep>[\'"])                # Equality, space(s) and then one of the separators
    #(?P<default>.*?)                   # Default value: any chars that is not a separator like previous one
    #(?P=sep)                           # Same separator as starting one
    #)?\s*                              # Default value for param is optional. Followed by space(s)
    (?P<desc>.*)                       # Any text that is not end of line (thanks to MULTILINE parameter)
    ''', re.VERBOSE | re.MULTILINE)

#Regular expression for finding format elements "see also" attribute (defined by @see)
pattern_format_element_seealso = re.compile('''@see\s*(?P<see>.*)''', re.VERBOSE | re.MULTILINE)

#Regular expression for finding 2 expressions in quotes, separated by comma (as in template("1st","2nd") )
#Used when parsing output formats
## pattern_parse_tuple_in_quotes = re.compile('''
##      (?P<sep1>[\'"])
##      (?P<val1>.*)
##      (?P=sep1)
##      \s*,\s*
##      (?P<sep2>[\'"])
##      (?P<val2>.*)
##      (?P=sep2)
##      ''', re.VERBOSE | re.MULTILINE)    

def format_record(recID, of, ln=cdslang, verbose=0, search_pattern=None, xml_record=None, uid=None):
    """
    Formats a record given output format. Main entry function of bibformat engine.
    
    Returns a formatted version of the record in
    the specified language, search pattern, and with the specified output format.
    The function will define which format template must be applied.

    You can either specify an record ID to format, or give its xml representation.
    if 'xml_record' != None, then use it instead of recID.

    'uid' allows to grant access to some functionalities on a page depending
    on the user's priviledges.    
    
    @param recID the ID of record to format
    @param of an output format code (or short identifier for the output format)
    @param ln the language to use to format the record
    @param verbose the level of verbosity from 0 to 9 (O: silent,
                                                       5: errors,
                                                       7: errors and warnings, stop if error in format elements
                                                       9: errors and warnings, stop if error (debug mode ))
    @param search_pattern the context in which this record was asked to be formatted (User request in web interface)
    @param xml_record an xml string representing the record to format
    @param uid the user id of the person who will view the formatted page
    @return formatted record
    """
    errors_ = []
   
    #Test record existence
    if xml_record == None and record_exists(recID) == 0:
        #Record does not exist
        error = get_msgs_for_code_list([("ERR_BIBFORMAT_NO_RECORD_FOUND_FOR_PATTERN", "recid:%s" % recID)],
                                       file='error', ln=cdslang)
        errors_.append(error)
        if verbose == 0:
            register_errors(error, 'error')
        return ("", errors_)

    #Create a BibFormat Object to give that contain record and context    
    bfo = BibFormatObject(recID, ln, search_pattern, xml_record, uid)

        
    #Find out which format template to use based on record and output format.
    template = decide_format_template(bfo, of)

    if template == None:
        error = get_msgs_for_code_list([("ERR_BIBFORMAT_NO_TEMPLATE_FOUND", of)],
                                       file='error', ln=cdslang)
        errors_.append(error)
        if verbose == 0:
            register_errors(error, 'error')
        elif verbose > 5:
            return error[0][1]  
        return ""

    #Format with template
    (out, errors) = format_with_format_template(template, bfo, verbose)
    errors_.extend(errors)
    
    return out

def decide_format_template(bfo, of):
    """
    Returns the format template name that should be used for formatting
    given output format and BibFormatObject.

    Look at of rules, and take the first matching one.
    If no rule matches, returns None

    To match we ignore lettercase and spaces before and after value of
    rule and value of record

    @param bfo a BibFormatObject
    @param of the code of the output format to use
    """

    output_format = get_output_format(of)

    for rule in output_format['rules']:
        value = bfo.field(rule['field']).strip()#Remove spaces
        pattern = rule['value'].strip() #Remove spaces
        if re.match(pattern, value, re.IGNORECASE) != None:
            return rule['template']

    template = output_format['default']
    if template != '':
        return template
    else:
        return None
    
def format_with_format_template(format_template_filename, bfo, verbose=0, format_template_code=None):
    """
    Format a record given a format template. Also returns errors
    
    Returns a formatted version of the record represented by bfo,
    in the language specified in bfo, and with the specified format template.

    Parameter format_template_filename will be ignored if format_template_code is provided.
    This allows to preview format code without having to save file on disk
    
    @param format_template_filename the dilename of a format template
    @param bfo the object containing parameters for the current formatting
    @param format_template_code if not empty, use code as template instead of reading format_template_filename (used for previews)
    @param verbose the level of verbosity from 0 to 9 (O: silent,
                                                       5: errors,
                                                       7: errors and warnings,
                                                       9: errors and warnings, stop if error (debug mode ))
    @return tuple (formatted text, errors)
    """
    errors_ = []
    if format_template_code != None:
        format_content = str(format_template_code)
    else:
        format_content = get_format_template(format_template_filename)['code']
  
    localized_format = filter_languages(format_content, bfo.lang)
    (evaluated_format, errors) = eval_format_template_elements(localized_format, bfo, verbose)
    errors_ = errors
 
    return (evaluated_format, errors)


def eval_format_template_elements(format_template, bfo, verbose=0):
    """
    Evalutes the format elements of the given template and replace each element with its value.
    Also returns errors.
    
    Prepare the format template content so that we can directly replace the marc code by their value.
    This implies: 1) Look for special tags
                  2) replace special tags by their evaluation
                  
    @param format_template the format template code
    @param bfo the object containing parameters for the current formatting
    @param verbose the level of verbosity from 0 to 9 (O: silent,
                                                       5: errors,
                                                       7: errors and warnings,
                                                       9: errors and warnings, stop if error (debug mode ))
    @return tuple (result, errors)
    """
    errors_ = []
    
    #First define insert_element_code(match), used in re.sub() function
    def insert_element_code(match):
        """
        Analyses 'match', interpret the corresponding code, and return the result of the evaluation.

        Called by substitution in 'eval_format_template_elements(...)'

        @param match a match object corresponding to the special tag that must be interpreted
        """

        function_name = match.group("function_name")
        
        format_element = get_format_element(function_name, verbose)
        params = {}
        #look for function parameters given in format template code
        all_params = match.group('params')
        if all_params != None:
            function_params_iterator = pattern_function_params.finditer(all_params)
            for param_match in function_params_iterator:
                name = param_match.group('param')
                value = param_match.group('value')
                params[name] = value

        #Evaluate element with params and return (Do not return errors)
        (result, errors) = eval_format_element(format_element, bfo, params, verbose)
        errors_ = errors
        return result
        
    
    #Substitute special tags in the format by our own text.
    #Special tags have the form <BNE_format_element_name [param="value"]* />
    format = pattern_tag.sub(insert_element_code, format_template)
    
    return (format, errors_)


def eval_format_element(format_element, bfo, parameters={}, verbose=0):
    """
    Returns the result of the evaluation of the given format element
    name, with given BibFormatObject and parameters. Also returns
    the errors of the evaluation.

    @param format_element a format element structure as returned by get_format_element
    @param bfo a BibFormatObject used for formatting
    @param parameters a dict of parameters to be used for formatting. Key is parameter and value is value of parameter
    @param verbose the level of verbosity from 0 to 9 (O: silent,
                                                       5: errors,
                                                       7: errors and warnings,
                                                       9: errors and warnings, stop if error (debug mode ))

    @return tuple (result, errors)
    """
    
    errors = []
    #Load special values given as parameters
    prefix = parameters.get('prefix', "")
    suffix = parameters.get('suffix', "")
    default_value = parameters.get('default', "")
    
    #3 possible cases:
    #a) format element file is found: we execute it
    #b) format element file is not found, but exist in tag table (e.g. bfe_isbn)
    #c) format element is totally unknown. Do nothing or report error
    
    if format_element != None and format_element['type'] == "python":
        #a)
        #We found an element with the tag name, of type "python"
        #Prepare a dict 'params' to pass as parameter to 'format' function of element
        params = {}

        #look for parameters defined in format element
        #fill them with specified default values and values
        #given as parameters
        for param in format_element['attrs']['params']:
            name = param['name']
            default = param['default']
            params[name] = parameters.get(name, default)
            
        #Add BibFormatObject
        params['bfo'] = bfo

        #execute function with given parameters and return result.
        output_text = ""
        function = format_element['code']
        
        try:
            output_text = apply(function, (), params)
        except Exception, e:
            output_text = ""
            name = format_element['attrs']['name']
            error = ("ERR_BIBFORMAT_EVALUATING_ELEMENT", name, str(params))
            errors.append(error)
            if verbose == 0:
                register_errors(errors, 'error')
            elif verbose >=5:
                tb = sys.exc_info()[2]
                error_string = get_msgs_for_code_list(error, file='error', ln=cdslang)
                stack = traceback.format_exception(Exception, e, tb, limit=None)
                output_text = '<b><span style="color: rgb(255, 0, 0);">'+error_string[0][1] + "".join(stack) +'</span></b> '


        if output_text == None:
            output_text = ""
        else:
            output_text = str(output_text)
     
        #Add prefix and suffix if they have been given as parameters and if
        #the evaluation of element is not empty
        if output_text.strip() != "":
            output_text = prefix + output_text + suffix

        #Add the default value if output_text is empty
        if output_text == "":
            output_text = default_value

        return (output_text, errors)
    
    elif format_element != None and format_element['type'] =="field":
        #b)
        #We have not found an element in files that has the tag name. Then look for it
        #in the table "tag"
        #
        # <BFE_LABEL_IN_TAG prefix = "" suffix = "" separator = "" nbMax="" />
        #

        #Load special values given as parameters
        separator = parameters.get('separator ', "")
        nbMax = parameters.get('nbMax', "")
        
        #Get the fields tags that have to be printed
        tags = format_element['attrs']['tags']

        output_text = []

        #Get values corresponding to tags
        for tag in tags:
            values = bfo.fields(tag)#Retrieve record values for tag
            if len(values)>0 and isinstance(values[0], dict):#flatten dict to its values only
                values_list = map(lambda x: x.values(), values)
                #output_text.extend(values)
                for values in values_list:
                    output_text.extend(values)
            else:
                output_text.extend(values)

        if nbMax != "":
            try:
                nbMax = int(nbMax)
                output_text = output_text[:nbMax]
            except:
                name = format_element['attrs']['name']
                error = ("ERR_BIBFORMAT_NBMAX_NOT_INT", name)
                errors.append(error)
                if verbose < 5:
                    register_errors(error, 'error')
                elif verbose >=5:
                    error_string = get_msgs_for_code_list(error, file='error', ln=cdslang)
                    output_text = output_text.append(error_string[0][1])


        #Add prefix and suffix if they have been given as parameters and if
        #the evaluation of element is not empty.
        #If evaluation is empty string, return default value if it exists. Else return empty string
        if ("".join(output_text)).strip() != "":
            return (prefix + separator.join(output_text) + suffix, errors)
        else:
            #Return default value
            return (default_value, errors)
    else:
        #c) Element is unknown
        error = get_msgs_for_code_list([("ERR_BIBFORMAT_CANNOT_RESOLVE_ELEMENT_NAME", format_element)],
                                    file='error', ln=cdslang)
        errors.append(error)
        if verbose < 5:
            register_errors(error, 'error')
            return ("", errors)
        elif verbose >=5:
            if verbose >= 9:
                sys.exit(error[0][1])
            return ('<b><span style="color: rgb(255, 0, 0);">'+error[0][1]+'</span></b>', errors)

    
def filter_languages(format_template, ln='en'):
    """
    Filters the language tags that do not correspond to the specified language.
    
    @param format_template the format template code
    @param ln the language that is NOT filtered out from the template
    @return the format template with unnecessary languages filtered out
    """
    #First define search_lang_tag(match) and clean_language_tag(match), used
    #in re.sub() function
    def search_lang_tag(match):
        """
        Searches for the <lang>...</lang> tag and remove inner localized tags
        such as <en>, <fr>, that are not current_lang.

        If current_lang cannot be found inside <lang> ... </lang>, try to use 'cdslang'

        @param match a match object corresponding to the special tag that must be interpreted
        """
        current_lang = ln
        def clean_language_tag(match):
            """
            Return tag text content if tag language of match is output language.

            Called by substitution in 'filter_languages(...)'

            @param match a match object corresponding to the special tag that must be interpreted
            """
            if match.group(1) == current_lang:
                return match.group(2)
            else:
                return ""
            #End of clean_language_tag

            
        lang_tag_content = match.group("langs")
        #Try to find tag with current lang. If it does not exists, then current_lang
        #becomes cdslang until the end of this replace
        pattern_current_lang = re.compile(r"<"+current_lang+"\s*>(.*?)</"+current_lang+"\s*>")
        if re.search(pattern_current_lang, lang_tag_content) == None:
            current_lang = cdslang

        cleaned_lang_tag = ln_pattern.sub(clean_language_tag, lang_tag_content)
        return cleaned_lang_tag
        #End of search_lang_tag

      
    filtered_format_template = pattern_lang.sub(search_lang_tag, format_template)
    return filtered_format_template

    
def parse_tag(tag):
    """
    Parse a marc code and decompose it in a table with: 0-tag 1-indicator1 2-indicator2 3-subfield

    The first 3 chars always correspond to tag.
    The indicators are optional. However they must both be indicated, or both ommitted.
    If indicators are ommitted or indicated with underscore '_', they mean "No indicator".
    The subfield is optional. It can optionally be preceded by a dot '.' or '$$' or '$'

    Any of the chars can be replaced by wildcard %

    THE FUNCTION DOES NOT CHECK WELLFORMNESS OF 'tag'
    
    Any empty chars is not considered
    
    For example:
    >> parse_tag('245COc') = ['245', 'C', 'O', 'c']
    >> parse_tag('245C_c') = ['245', 'C', '', 'c']
    >> parse_tag('245__c') = ['245', '', '', 'c']
    >> parse_tag('245__$$c') = ['245', '', '', 'c']
    >> parse_tag('245__$c') = ['245', '', '', 'c']
    >> parse_tag('245  $c') = ['245', '', '', 'c']
    >> parse_tag('245  $$c') = ['245', '', '', 'c']
    >> parse_tag('245__.c') = ['245', '', '', 'c']
    >> parse_tag('245  .c') = ['245', '', '', 'c']
    >> parse_tag('245C_$c') = ['245', 'C', '', 'c']
    >> parse_tag('245CO$$c') = ['245', 'C', 'O', 'c']
    >> parse_tag('245C_.c') = ['245', 'C', '', 'c']
    >> parse_tag('245$c') = ['245', '', '', 'c']
    >> parse_tag('245.c') = ['245', '', '', 'c']
    >> parse_tag('245$$c') = ['245', '', '', 'c']
    >> parse_tag('245__%') = ['245', '', '', '']
    >> parse_tag('245__$$%') = ['245', '', '', '']
    >> parse_tag('245__$%') = ['245', '', '', '']
    >> parse_tag('245  $%') = ['245', '', '', '']
    >> parse_tag('245  $$%') = ['245', '', '', '']
    >> parse_tag('245$%') = ['245', '', '', '']
    >> parse_tag('245.%') = ['245', '', '', '']
    >> parse_tag('245$$%') = ['245', '', '', '']
    >> parse_tag('2%5$$a') = ['2%5', '', '', 'a']
    """

    p_tag =  ['', '', '', '']
    tag = tag.replace(" ", "") #Remove empty characters
    tag = tag.replace("$", "") #Remove $ characters
    tag = tag.replace(".", "") #Remove . characters
    #tag = tag.replace("_", "") #Remove _ characters
    
    p_tag[0] = tag[0:3] #tag
    if len(tag) == 4:
        p_tag[3] = tag[3] #subfield
        
    elif len(tag) == 5:
        ind1 = tag[3]#indicator 1
        if ind1 != "_":
            p_tag[1] = ind1
            
        ind2 = tag[4]#indicator 2
        if ind2 != "_":
            p_tag[2] = ind2
            
    elif len(tag) == 6:
        p_tag[3] = tag[5]#subfield
        
        ind1 = tag[3]#indicator 1
        if ind1 != "_":
            p_tag[1] = ind1
            
        ind2 = tag[4]#indicator 2
        if ind2 != "_":
            p_tag[2] = ind2
            
    return p_tag

def get_format_template(filename, with_attributes=False):
    """
    Returns the structured content of the given formate template.

    if 'with_attributes' is True, returns the name and description. Else 'attrs' is not
    returned as key in dictionary (it might, if it has already been loaded previously)

    Caution: the code of the template has all % chars escaped as %%
    (beause we use python formatting capabilites)
    
    {'code':"<b>Some template code</b>"
     'attrs': {'name': "a name", 'description': "a description"}
    }

    @param filename the filename of an format template
    @param with_attributes if True, fetch the attributes (names and description) for format'
    @return strucured content of format template
    """
    #Get from cache whenever possible
    global format_templates_cache

    if not filename.endswith("."+format_template_extension):
            return None
        
    if format_templates_cache.has_key(filename):
        #If we must return with attributes and template exist in cache with attributes
        #then return cache. Else reload with attributes
        if with_attributes == True and format_templates_cache[filename].has_key('attrs'):
            return format_templates_cache[filename]

    format_template = {'code':""}
    try:
        
        path = "%s%s%s" % (templates_path, os.sep, filename)

        format_file = open(path)
        format_content = format_file.read()
        format_file.close()

        #Load format template code
        #Remove name and description
        code_and_description = pattern_format_template_name.sub("", format_content)
        code = pattern_format_template_desc.sub("", code_and_description)
        
        # Escape % chars in code (because we will use python formatting capabilities)
        code = code.replace('%','%%')   
        format_template['code'] = code

    except Exception, e:
        errors = get_msgs_for_code_list([("ERR_BIBFORMAT_CANNOT_READ_TEMPLATE_FILE", filename, str(e))],
                                        file='error', ln=cdslang)
        register_errors(errors, 'error')

    #Save attributes if necessary
    if with_attributes:
        format_template['attrs'] = get_format_template_attrs(filename)

    #cache and return 
    format_templates_cache[filename] = format_template
    return format_template


def get_format_templates(with_attributes=False):
    """
    Returns the list of all format templates

    if 'with_attributes' is True, returns the name and description. Else 'attrs' is not
    returned as key in each dictionary (it might, if it has already been loaded previously)

    [{'code':"<b>Some template code</b>"
      'attrs': {'name': "a name", 'description': "a description"}
     },
    ...
    }
    @param with_attributes if True, fetch the attributes (names and description) for formats
    """
    format_templates = {}
    files = os.listdir(templates_path)
    
    for filename in files:
        if filename.endswith("."+format_template_extension):
            format_templates[filename] = get_format_template(filename, with_attributes)
                       
    return format_templates

def get_format_template_attrs(filename):
    """
    Returns the attributes of the format template with given filename
    
    The attributes are {'name', 'description'}
    Caution: the function does not check that path exists or
    that the format element is valid.
    @param the path to a format element
    """
    attrs = {}
    attrs['name'] = ""
    attrs['description'] = ""
    try:
        template_file = open("%s%s%s"%(templates_path, os.sep, filename))
        code = template_file.read()
        template_file.close()

        match = pattern_format_template_name.search(code)
        if match != None:
            attrs['name'] = match.group('name')
        else:
            attrs['name'] = filename
        
        match = pattern_format_template_desc.search(code)
        if match != None:
            attrs['description'] = match.group('desc').rstrip('.')
    except Exception, e:
        errors = get_msgs_for_code_list([("ERR_BIBFORMAT_CANNOT_READ_TEMPLATE_FILE", filename, str(e))],
                                        file='error', ln=cdslang)
        register_errors(errors, 'error')
        attrs['name'] = filename

    return attrs


def get_format_element(element_name, verbose=0, with_built_in_params=False):
    """
    Returns the format element structured content.

    Return None if element cannot be loaded (file not found, not readable or
    invalid)

    The returned structure is {'attrs': {some attributes in dict. See get_format_element_attrs_from_*} 
                               'code': the_function_code,
                               'type':"field" or "python" depending if element is defined in file or table}

    @param element_name the name of the format element to load
    @param verbose the level of verbosity from 0 to 9 (O: silent,
                                                       5: errors,
                                                       7: errors and warnings,
                                                       9: errors and warnings, stop if error (debug mode ))
    @param with_built_in_params if True, load the parameters built in all elements 
    @return a dictionary with format element attributes
    """
    #Get from cache whenever possible
    global format_elements_cache

    #Resolve filename and prepare 'name' as key for the cache
    filename = resolve_format_element_filename(element_name)
    if filename != None:
        name = filename.upper()
    else:
        name = element_name.upper()
        
    if format_elements_cache.has_key(name):
        element = format_elements_cache[name]
        if with_built_in_params == False or (with_built_in_params == True and element['attrs'].has_key('builtin_params') ):
            return element

    if filename == None:
        #element is maybe in tag table
        if bibformat_dblayer.tag_exists_for_name(element_name):
            format_element = {'attrs': get_format_element_attrs_from_table(element_name, with_built_in_params),
                              'code':None,
                              'type':"field"}
            #Cache and returns
            format_elements_cache[name] = format_element
            return format_element
        
        else:
            errors = get_msgs_for_code_list([("ERR_BIBFORMAT_FORMAT_ELEMENT_NOT_FOUND", element_name)],
                                            file='error', ln=cdslang)
            if verbose == 0:
                register_errors(errors, 'error')
            elif verbose >=5:
                sys.stderr.write(errors[0][1])  
            return None
    
    else:
        format_element = {}
        
        module_name = filename
        if module_name.endswith(".py"):
            module_name = module_name[:-3]
        #module = __import__(elements_import_path+"."+module_name)
         
        try:
            module = __import__(elements_import_path+"."+module_name)

            #Load last module in import path
            #For eg. load bibformat_elements in invenio.elements.bibformat_element
            #Used to keep flexibility regarding where elements directory is (for eg. test cases)
            components = elements_import_path.split(".")
            for comp in components[1:]:
                module = getattr(module, comp) 

            function_format  = module.__dict__[module_name].format


            format_element['code'] = function_format
            format_element['attrs'] = get_format_element_attrs_from_function(function_format,
                                                                             element_name,
                                                                             with_built_in_params)
            format_element['type'] = "python"
    
            #cache and return
            format_elements_cache[name] = format_element
            return format_element
        except Exception, e:
            errors = get_msgs_for_code_list([("ERR_BIBFORMAT_FORMAT_ELEMENT_NOT_FOUND", element_name)],
                                            file='error', ln=cdslang)
            if verbose == 0:
                register_errors(errors, 'error')
            elif verbose >= 5:
                sys.stderr.write(str(e))
                sys.stderr.write(errors[0][1])
                if verbose >= 7:
                    raise e
            return None

        
def get_format_elements(with_built_in_params=False):
    """
    Returns the list of format elements attributes as dictionary structure
    
    Elements declared in files have priority over element declared in 'tag' table
    The returned object has this format:
    {element_name1: {'attrs': {'description':..., 'seealso':...
                               'params':[{'name':..., 'default':..., 'description':...}, ...]
                               'builtin_params':[{'name':..., 'default':..., 'description':...}, ...]
                              },
                     'code': code_of_the_element
                    },
     element_name2: {...},
     ...}

     Returns only elements that could be loaded (not error in code)
    
    @return a dict of format elements with name as key, and a dict as attributes
    @param with_built_in_params if True, load the parameters built in all elements 
    """
    format_elements = {}
      
    mappings = bibformat_dblayer.get_all_name_tag_mappings()

    for name in mappings:
        format_elements[name.upper().replace(" ", "_").strip()] = get_format_element(name, with_built_in_params=with_built_in_params)
    
    files = os.listdir(elements_path)
    for filename in files:
        filename_test = filename.upper().replace(" ", "_")
        if filename_test.endswith(".PY") and filename != "__INIT__.PY":
            if filename_test.startswith("BFE_"):
                filename_test = filename_test[4:]
            element_name = filename_test[:-3]
            element = get_format_element(element_name, with_built_in_params=with_built_in_params)
            if element != None:
                format_elements[element_name] = element
         
    return format_elements

def get_format_element_attrs_from_function(function, element_name, with_built_in_params=False):
    """
    Returns the attributes of the function given as parameter.
    
    It looks for standard parameters of the function, default
    values and comments in the docstring.
    The attributes are {'description', 'seealso':['element.py', ...],
    'params':{name:{'name', 'default', 'description'}, ...], name2:{}}
    
    The attributes are {'name' : "name of element" #basically the name of 'name' parameter
                        'description': "a string description of the element",
                        'seealso' : ["element_1.py", "element_2.py", ...] #a list of related elements
                        'params': [{'name':"param_name",   #a list of parameters for this element (except 'bfo')
                                    'default':"default value",
                                    'description': "a description"}, ...],
                        'builtin_params': {name: {'name':"param_name",#the parameters builtin for all elem of this kind
                                            'default':"default value",
                                            'description': "a description"}, ...},
                       }
    @param function the formatting function of a format element
    @param element_name the name of the element
    @param with_built_in_params if True, load the parameters built in all elements 
    """
 
    attrs = {}
    attrs['description'] = ""
    attrs['name'] = element_name.replace(" ", "_").upper()
    attrs['seealso'] = []

    docstring = function.__doc__
    if isinstance(docstring, str):
        #Look for function description in docstring
        #match = pattern_format_element_desc.search(docstring)
        description = docstring.split("@param")[0]
        description = description.split("@see")[0]
        attrs['description'] = description.strip().rstrip('.')

        #Look for @see in docstring
        match = pattern_format_element_seealso.search(docstring)
        if match != None:
            elements = match.group('see').rstrip('.').split(",")
            for element in elements:
                attrs['seealso'].append(element.strip())

    params = {}
    #Look for parameters in function definition
    (args, varargs, varkw, defaults) = inspect.getargspec(function)

    #Prepare args and defaults_list such that we can have a mapping from args to defaults
    args.reverse()
    if defaults != None:
        defaults_list = list(defaults)
        defaults_list.reverse()
    else:
        defaults_list = []
        
    for arg, default in map(None, args, defaults_list):
        if arg == "bfo":
            continue #Don't keep this as parameter. It is hidden to users, and exists in all elements of this kind
        param = {}
        param['name'] = arg
        if default == None:
            param['default'] = "" #In case no check is made inside element, we prefer to print "" (nothing) than None in output
        else:
            param['default'] = default
        param['description'] = "(no description provided)"
     
        params[arg] = param

    if isinstance(docstring, str):
        #Look for @param descriptions in docstring.
        #Add description to existing parameters in params dict
        params_iterator = pattern_format_element_params.finditer(docstring)
        for match in params_iterator:
            name = match.group('name')
            if params.has_key(name):
                params[name]['description'] = match.group('desc').rstrip('.')

    attrs['params'] = params.values()
    
    #Load built-in parameters if necessary
    if with_built_in_params == True:
        
        builtin_params = []
        #Add 'prefix' parameter
        param_prefix = {}
        param_prefix['name'] = "prefix"
        param_prefix['default'] = ""
        param_prefix['description'] = "A prefix printed only if the record has a value for this element"
        builtin_params.append(param_prefix)

        #Add 'suffix' parameter
        param_suffix = {}
        param_suffix['name'] = "suffix"
        param_suffix['default'] = ""
        param_suffix['description'] = "A suffix printed only if the record has a value for this element"
        builtin_params.append(param_suffix)

        #Add 'default' parameter
        param_default = {}
        param_default['name'] = "default"
        param_default['default'] = ""
        param_default['description'] = "A default value printed if the record has no value for this element"
        builtin_params.append(param_default)

        attrs['builtin_params'] = builtin_params
   
    return attrs

def get_format_element_attrs_from_table(element_name, with_built_in_params=False):
    """
    Returns the attributes of the format element with given name in 'tag' table.

    Returns None if element_name does not exist in tag table.

    The attributes are {'name' : "name of element" #basically the name of 'element_name' parameter
                        'description': "a string description of the element",
                        'seealso' : [] #a list of related elements. Always empty in this case
                        'params': [],  #a list of parameters for this element. Always empty in this case
                        'builtin_params': [{'name':"param_name", #the parameters builtin for all elem of this kind
                                            'default':"default value",
                                            'description': "a description"}, ...],
                        'tags':["950.1", 203.a] #the list of tags printed by this element
                       }
    
    @param element_name an element name in database
    @param element_name the name of the element
    @param with_built_in_params if True, load the parameters built in all elements 
    """

    attrs = {}
    tags = bibformat_dblayer.get_tags_from_name(element_name)
    field_label = "field"
    if len(tags)>1:
        field_label = "fields"

    attrs['description'] = "Prints %s %s of the record" % (field_label, ", ".join(tags))
    attrs['name'] = element_name.replace(" ", "_").upper()
    attrs['seealso'] = []
    attrs['params'] = []
    attrs['tags'] = tags
    
    #Load built-in parameters if necessary
    if with_built_in_params == True:
        builtin_params = []
        
        #Add 'prefix' parameter
        param_prefix = {}
        param_prefix['name'] = "prefix"
        param_prefix['default'] = ""
        param_prefix['description'] = "A prefix printed only if the record has a value for this element"
        builtin_params.append(param_prefix)

        #Add 'suffix' parameter
        param_suffix = {}
        param_suffix['name'] = "suffix"
        param_suffix['default'] = ""
        param_suffix['description'] = "A suffix printed only if the record has a value for this element"
        builtin_params.append(param_suffix)

        #Add 'separator' parameter
        param_separator = {}
        param_separator['name'] = "separator"
        param_separator['default'] = " "
        param_separator['description'] = "A separator between elements of the field"
        builtin_params.append(param_separator)

        #Add 'nbMax' parameter
        param_nbMax = {}
        param_nbMax['name'] = "nbMax"
        param_nbMax['default'] = ""
        param_nbMax['description'] = "The maximum number of values to print for this element. No limit if not specified"
        builtin_params.append(param_nbMax)

        #Add 'default' parameter
        param_default = {}
        param_default['name'] = "default"
        param_default['default'] = ""
        param_default['description'] = "A default value printed if the record has no value for this element"
        builtin_params.append(param_default)

        attrs['builtin_params'] = builtin_params

    return attrs
      
def get_output_format(code, with_attributes=False, verbose=0):
    """
    Returns the structured content of the given output format

    If 'with_attributes' is True, also returns the names and description of the output formats,
    else 'attrs' is not returned in dict (it might, if it has already been loaded previously).

    if output format corresponding to 'code' is not found return an empty structure.
    
    See get_output_format_attrs() to learn more on the attributes
    

    {'rules': [ {'field': "980__a",
                 'value': "PREPRINT",
                 'template': "filename_a.bft",
                },
                {...}
              ],
     'attrs': {'names': {'generic':"a name", 'sn':{'en': "a name", 'fr':"un nom"}, 'ln':{'en':"a long name"}}
               'description': "a description"
               'code': "fnm1",
               'content_type': "application/ms-excel"
              } 
     'default':"filename_b.bft"
    }
    
    @param code the code of an output_format
    @param with_attributes if True, fetch the attributes (names and description) for format
    @param verbose the level of verbosity from 0 to 9 (O: silent,
                                                       5: errors,
                                                       7: errors and warnings,
                                                       9: errors and warnings, stop if error (debug mode ))
    @return strucured content of output format
    """
    output_format = {'rules':[], 'default':""}
    filename = resolve_output_format_filename(code, verbose)
    
    if filename == None:
        errors = get_msgs_for_code_list([("ERR_BIBFORMAT_OUTPUT_FORMAT_CODE_UNKNOWN", code)],
                                        file='error', ln=cdslang)
        register_errors(errors, 'error')
        if with_attributes == True: #Create empty attrs if asked for attributes
            output_format['attrs'] = get_output_format_attrs(code, verbose)
        return output_format
    
    #Get from cache whenever possible
    global format_outputs_cache
    if format_outputs_cache.has_key(filename):
        #If was must return with attributes but cache has not attributes, then load attributes
        if with_attributes == True and not format_outputs_cache[filename].has_key('attrs'):
            format_outputs_cache[filename]['attrs'] = get_output_format_attrs(code, verbose)

        return format_outputs_cache[filename]

    try:
        if with_attributes == True:
            output_format['attrs'] = get_output_format_attrs(code, verbose)

        path = "%s%s%s" % (outputs_path, os.sep, filename )
        format_file = open(path)

        current_tag = ''
        for line in format_file:
            line = line.strip()
            if line == "":
                #ignore blank lines
                continue
            if line.endswith(":"):
                #retrieve tag
                clean_line = line.rstrip(": \n\r") #remove : spaces and eol at the end of line
                current_tag = "".join(clean_line.split()[1:]).strip() #the tag starts at second position
            elif line.find('---') != -1:
                words = line.split('---')
                template = words[-1].strip()
                condition = ''.join(words[:-1])
                value = ""
                
                output_format['rules'].append({'field': current_tag,
                                               'value': condition,
                                               'template': template,
                                               })
              
            elif line.find(':') != -1:
                #Default case
                default = line.split(':')[1].strip()
                output_format['default'] = default

    except Exception, e:
        errors = get_msgs_for_code_list([("ERR_BIBFORMAT_CANNOT_READ_OUTPUT_FILE", filename, str(e))],
                                        file='error', ln=cdslang)
        register_errors(errors, 'error')
             
    #cache and return 
    format_outputs_cache[filename] = output_format
    return output_format

def get_output_format_attrs(code, verbose=0):
    """
    Returns the attributes of an output format.

    The attributes contain 'code', which is the short identifier of the output format
    (to be given as parameter in format_record function to specify the output format),
    'description', a description of the output format, and 'names', the localized names
    of the output format. If 'content_type' is specified then the search_engine will
    send a file with this content type and with result of formatting as content to the user.
    The 'names' dict always contais 'generic', 'ln' (for long name) and 'sn' (for short names)
    keys. 'generic' is the default name for output format. 'ln' and 'sn' contain long and short
    localized names of the output format. Only the languages for which a localization exist
    are used.

    {'names': {'generic':"a name", 'sn':{'en': "a name", 'fr':"un nom"}, 'ln':{'en':"a long name"}}
     'description': "a description"
     'code': "fnm1",
     'content_type': "application/ms-excel"
    } 

    @param code the short identifier of the format
    @param verbose the level of verbosity from 0 to 9 (O: silent,
                                                       5: errors,
                                                       7: errors and warnings,
                                                       9: errors and warnings, stop if error (debug mode ))
    @return strucured content of output format attributes
    """
    if code.endswith("."+format_output_extension):
        code = code[:-(len(format_output_extension) + 1)]
    attrs = {'names':{'generic':"",
                      'ln':{},
                      'sn':{}},
             'description':'',
             'code':code.upper(),
             'content_type':""}
    
    filename = resolve_output_format_filename(code, verbose)
    if filename == None:
        return attrs
     
    attrs['names'] = bibformat_dblayer.get_output_format_names(code)
    attrs['description'] = bibformat_dblayer.get_output_format_description(code)
    attrs['content_type'] = bibformat_dblayer.get_output_format_content_type(code)
    
    return attrs
        
def get_output_formats(with_attributes=False):
    """
    Returns the list of all output format, as a dictionary with their filename as key

    If 'with_attributes' is True, also returns the names and description of the output formats,
    else 'attrs' is not returned in dicts (it might, if it has already been loaded previously).
    
    See get_output_format_attrs() to learn more on the attributes
    
    {'filename_1.bfo': {'rules': [ {'field': "980__a",
                                    'value': "PREPRINT",
                                    'template': "filename_a.bft",
                                   },
                                   {...}
                                 ],
                        'attrs': {'names': {'generic':"a name", 'sn':{'en': "a name", 'fr':"un nom"}, 'ln':{'en':"a long name"}}
                                  'description': "a description"
                                  'code': "fnm1"
                                 } 
                        'default':"filename_b.bft"
                       },
                      
     'filename_2.bfo': {...},
      ...
    }
    @return the list of output formats
    """
    output_formats = {}
    files = os.listdir(outputs_path)
    
    for filename in files:
        if filename.endswith("."+format_output_extension):
            code = "".join(filename.split(".")[:-1])
            output_formats[filename] = get_output_format(code, with_attributes)

    return output_formats
                
def get_kb_mapping(kb, string, default=""):
    """
    Returns the value of the string' in the knowledge base 'kb'.
    
    If kb does not exist or string does not exist in kb, returns 'default'
    string value.
    
    @param kb a knowledge base name
    @param string a key in a knowledge base
    @param default a default value if 'string' is not in 'kb'
    @return the value corresponding to the given string in given kb 
    """
  
    global kb_mappings_cache
    
    if kb_mappings_cache.has_key(kb):
        kb_cache = kb_mappings_cache[kb]
        if kb_cache.has_key(string):
            value = kb_mappings_cache[kb][string]
            if value == None:
                return default
            else:
                return value
    else:
        #Precreate for caching this kb
        kb_mappings_cache[kb] = {}
        
    value = bibformat_dblayer.get_kb_mapping_value(kb, string)

    kb_mappings_cache[kb][str(string)] = value
    if value == None:
        return default
    else:
        return value

def resolve_format_element_filename(string):
    """
    Returns the filename of element corresponding to string

    This is necessary since format templates code call
    elements by ignoring case, for eg. <BFE_AUTHOR> is the
    same as <BFE_author>.
    It is also recommended that format elements filenames are
    prefixed with bfe_ . We need to look for these too.

    The name of the element has to start with "BFE_".
    
    @param name a name for a format element
    @return the corresponding filename, with right case
    """
    
    if not string.endswith(".py"):
        name = string.replace(" ", "_").upper() +".PY"
    else:
        name = string.replace(" ", "_").upper()
        
    files = os.listdir(elements_path)
    for filename in files:
        test_filename = filename.replace(" ", "_").upper()
        
        if test_filename == name or \
        test_filename == "BFE_" + name or \
        "BFE_" + test_filename == name:
            return filename

    #No element with that name found
    #Do not log error, as it might be a normal execution case:
    #element can be in database
    return None

def resolve_output_format_filename(code, verbose=0):
    """
    Returns the filename of output corresponding to code

    This is necessary since output formats names are not case sensitive
    but most file systems are.
    
    @param code the code for an output format
    @param verbose the level of verbosity from 0 to 9 (O: silent,
                                                       5: errors,
                                                       7: errors and warnings,
                                                       9: errors and warnings, stop if error (debug mode ))
    @return the corresponding filename, with right case, or None if not found
    """
    code = re.sub(r"[^.0-9a-zA-Z]", "", code) #Remove non alphanumeric chars (except .)
    if not code.endswith("."+format_output_extension):
        code = re.sub(r"\W", "", code)
        code += "."+format_output_extension

    files = os.listdir(outputs_path)
    for filename in files:
        if filename.upper() == code.upper():
            return filename

    #No output format  with that name found
    errors = get_msgs_for_code_list([("ERR_BIBFORMAT_CANNOT_RESOLVE_OUTPUT_NAME", code)],
                                    file='error', ln=cdslang)
    if verbose == 0:
        register_errors(errors, 'error')
    elif verbose >= 5:
        sys.stderr.write(errors[0][1])
        if verbose >= 9:
            sys.exit(errors[0][1])
    return None

def get_fresh_format_template_filename(name):
    """
    Returns a new filename and name for template with given name.
    
    Used when writing a new template to a file, so that the name
    has no space, is unique in template directory

    Returns (unique_filename, modified_name)
    
    @param a name for a format template
    @return the corresponding filename, and modified name if necessary
    """
    #name = re.sub(r"\W", "", name) #Remove non alphanumeric chars
    name = name.replace(" ", "_")
    filename = name
    filename = re.sub(r"[^.0-9a-zA-Z]", "", filename) #Remove non alphanumeric chars (except .)
    path = templates_path + os.sep + filename + "." + format_template_extension
    index = 1
    while os.path.exists(path):
        index += 1
        filename = name + str(index)
        path = templates_path + os.sep + filename + "." + format_template_extension

    if index > 1:
        returned_name = (name + str(index)).replace("_", " ")
    else:
        returned_name = name.replace("_", " ")
         
    return (filename + "." + format_template_extension, returned_name) #filename.replace("_", " "))

def get_fresh_output_format_filename(code):
    """
    Returns a new filename for output format with given code.
    
    Used when writing a new output format to a file, so that the code
    has no space, is unique in output format directory. The filename
    also need to be at most 6 chars long, as the convention is that
    filename == output format code (+ .extension)
    We return an uppercase code
    Returns (unique_filename, modified_code)
    
    @param code the code of an output format
    @return the corresponding filename, and modified code if necessary
    """
    #code = re.sub(r"\W", "", code) #Remove non alphanumeric chars
    code = code.upper().replace(" ", "_")
    code = re.sub(r"[^.0-9a-zA-Z]", "", code) #Remove non alphanumeric chars (except .)
    if len(code) > 6:
        code = code[:6]
    
    filename = code
    path = outputs_path + os.sep + filename + "." + format_output_extension
    index = 2
    while os.path.exists(path):
        filename = code + str(index)
        if len(filename) > 6:
            filename = code[:-(len(str(index)))]+str(index)
        index += 1
        path = outputs_path + os.sep + filename + "." + format_output_extension
        #We should not try more than 99999... Well I don't see how we could get there.. Sanity check.
        if index >= 99999:
            errors = get_msgs_for_code_list([("ERR_BIBFORMAT_NB_OUTPUTS_LIMIT_REACHED", code)],
                                            file='error', ln=cdslang)
            register_errors(errors, 'error')
            sys.exit("Output format cannot be named as %s"%code)
            
    return (filename + "." + format_output_extension, filename)

def clear_caches():
    """
    Clear the caches (Output Format, Format Templates and Format Elements)

    """
    global format_templates_cache, format_elements_cache , format_outputs_cache, kb_mappings_cache
    format_templates_cache = {}
    format_elements_cache = {}
    format_outputs_cache = {}
    kb_mappings_cache = {}
    
                
class BibFormatObject:
    """
    An object that encapsulates a record and associated methods, and that is given
    as parameter to all format elements 'format' function.
    The object is made specifically for a given formatting, i.e. it includes
    for example the language for the formatting.

    The object provides basic accessors to the record. For full access, one can get
    the record with get_record() and then use BibRecord methods on the returned object.
    """
    #The record
    record = None

    #The language in which the formatting has to be done
    lang = cdslang

    #A string pattern describing the context in which the record has to be formatted.
    #It represents the user request in web interface search
    search_pattern = None

    #The id of the record
    recID = 0

    #The user id of the person who will view the formatted page (if applicable)
    #This allows for example to print a "edit record" link for people
    #who have right to edit a record.
    uid = None
    
    def __init__(self, recID, ln=cdslang, search_pattern=None, xml_record=None, uid=None):
        """
        Creates a new bibformat object, with given record.

        You can either specify an record ID to format, or give its xml representation.
        if 'xml_record' != None, use 'xml_record' instead of recID for the record.

        'uid' allows to grant access to some functionalities on a page depending
        on the user's priviledges.
        
        @param recID the id of a record
        @param ln the language in which the record has to be formatted
        @param search_pattern the request used by the user in web interface
        @param xml_record a xml string of the record to format
        @param uid the user id of the person who will view the formatted page
        """
        if xml_record != None:
            #If record is given as parameter
            self.record = create_record(xml_record)[0]
            recID = record_get_field_value(self.record,"001")
            
        self.lang = ln
        self.search_pattern = search_pattern
        self.recID = recID
        self.uid = uid
        
    def get_record(self):
        """
        Returns the record of this BibFormatObject instance

        @return the record structure as returned by BibRecord
        """
        
        #Create record if necessary
        if self.record == None:
            record = create_record(get_xml(self.recID, 'xm'))
            self.record = record[0]

        return self.record
    
    def control_field(self, tag):
        """
        Returns the value of control field given by tag in record
        
        @param record the record to retrieve values from
        @param tag the marc code of a field
        @return value of field tag in record
        """
        if self.get_record() == None: #Case where BibRecord could not parse object
            return ''
        
        p_tag = parse_tag(tag)

        return record_get_field_value(self.get_record(),
                                      p_tag[0],
                                      p_tag[1],
                                      p_tag[2],
                                      p_tag[3])

    def field(self, tag):
        """
        Returns the value of the field corresponding to tag in the
        current record.

        if the value does not exist, return empty string
        @param record the record to retrieve values from
        @param tag the marc code of a field
        @return value of field tag in record
        """
        list_of_fields = self.fields(tag)
        if len(list_of_fields) > 0:
            return list_of_fields[0]
        else:
            return ""

    def fields(self, tag):
        """
        Returns the list of values corresonding to "tag".

        If tag has an undefined subcode (such as 999C5),
        the function returns a list of dictionaries, whoose keys
        are the subcodes and the values are the values of tag.subcode.
        If the tag has a subcode, simply returns list of values
        corresponding to tag.
        @param record the record to retrieve values from
        @param tag the marc code of a field
        @return values of field tag in record
        """
        if self.get_record() == None: #Case where BibRecord could not parse object
            return []
        
        p_tag = parse_tag(tag)
        if p_tag[3] != "":
            #Subcode has been defined. Simply returns list of values
            return record_get_field_values(self.get_record(),
                                           p_tag[0],
                                           p_tag[1],
                                           p_tag[2],
                                           p_tag[3])
        else:
            #Subcode is undefined. Returns list of dicts.
            #However it might be the case of a control field.
            list_of_dicts = []

            instances = record_get_field_instances(self.get_record(),
                                                   p_tag[0],
                                                   p_tag[1],
                                                   p_tag[2])
            for instance in instances:
                instance_dict = dict(instance[0])
                list_of_dicts.append(instance_dict)

            return list_of_dicts

    def kb(self, kb, string, default=""):
        """
        Returns the value of the "string" in the knowledge base "kb".
        
        If kb does not exist or string does not exist in kb,
        returns 'default' string or empty string if not specified.

        @param kb a knowledge base name
        @param string the string we want to translate
        @param default a default value returned if 'string' not found in 'kb'
        """
        return get_kb_mapping(kb, string, default)


def get_xml(recID, format='xm', decompress=zlib.decompress):
    """
    Returns an XML string of the record given by recID.

    The function builds the XML directly from the database,
    without using the standard formatting process.

    'format' allows to define the flavour of XML:
        - 'xm' for standard XML
        - 'marcxml' for MARC XML 
        - 'oai_dc' for OAI Dublin Core
        - 'xd' for XML Dublin Core

    If record does not exist, returns empty string.

    @param recID the id of the record to retrieve
    @return the xml string of the record
    """
    #_ = gettext_set_language(ln)

    out = ""

    # sanity check:
    record_exist_p = record_exists(recID)
    if record_exist_p == 0: # doesn't exist
        return out

    # print record opening tags, if needed:
    if format == "marcxml" or format == "oai_dc":
        out += "  <record>\n"
        out += "   <header>\n"
        for id in get_fieldvalues(recID, cfg_oai_id_field):
            out += "    <identifier>%s</identifier>\n" % id
        out += "    <datestamp>%s</datestamp>\n" % get_modification_date(recID)
        out += "   </header>\n"
        out += "   <metadata>\n"

    if format.startswith("xm") or format == "marcxml":
        # look for detailed format existence:
        query = "SELECT value FROM bibfmt WHERE id_bibrec='%s' AND format='%s'" % (recID, format)
        res = run_sql(query, None, 1)
        if res and record_exist_p == 1:
            # record 'recID' is formatted in 'format', so print it
            out += "%s" % decompress(res[0][0])
        else:
            # record 'recID' is not formatted in 'format' -- they are not in "bibfmt" table; so fetch all the data from "bibXXx" tables:
            if format == "marcxml":
                out += """    <record xmlns="http://www.loc.gov/MARC21/slim">\n"""
                out += "        <controlfield tag=\"001\">%d</controlfield>\n" % int(recID)
            elif format.startswith("xm"):
                out += """    <record>\n"""
                out += "        <controlfield tag=\"001\">%d</controlfield>\n" % int(recID)
            if record_exist_p == -1:
                # deleted record, so display only OAI ID and 980:
                oai_ids = get_fieldvalues(recID, cfg_oaiidtag)
                if oai_ids:
                    out += "<datafield tag=\"%s\" ind1=\"%s\" ind2=\"%s\"><subfield code=\"%s\">%s</subfield></datafield>\n" % \
                           (cfg_oaiidtag[0:3], cfg_oaiidtag[3:4], cfg_oaiidtag[4:5], cfg_oaiidtag[5:6], oai_ids[0])
                out += "<datafield tag=\"980\" ind1=\"\" ind2=\"\"><subfield code=\"c\">DELETED</subfield></datafield>\n"
            else:
                for digit1 in range(0, 10):
                    for digit2 in range(0, 10):
                        bx = "bib%d%dx" % (digit1, digit2)
                        bibx = "bibrec_bib%d%dx" % (digit1, digit2)
                        query = "SELECT b.tag,b.value,bb.field_number FROM %s AS b, %s AS bb "\
                                "WHERE bb.id_bibrec='%s' AND b.id=bb.id_bibxxx AND b.tag LIKE '%s%%' "\
                                "ORDER BY bb.field_number, b.tag ASC" % (bx, bibx, recID, str(digit1)+str(digit2))
                        res = run_sql(query)
                        field_number_old = -999
                        field_old = ""
                        for row in res:
                            field, value, field_number = row[0], row[1], row[2]
                            ind1, ind2 = field[3], field[4]
                            if ind1 == "_":
                                ind1 = ""
                            if ind2 == "_":
                                ind2 = ""
                            # print field tag
                            if field_number != field_number_old or field[:-1] != field_old[:-1]:
                                if format.startswith("xm") or format == "marcxml":

                                    fieldid = encode_for_xml(field[0:3])

                                    if field_number_old != -999:
                                        out += """        </datafield>\n"""

                                    out += """        <datafield tag="%s" ind1="%s" ind2="%s">\n""" % \
                                           (encode_for_xml(field[0:3]), encode_for_xml(ind1), encode_for_xml(ind2))

                                field_number_old = field_number
                                field_old = field
                            # print subfield value
                            if format.startswith("xm") or format == "marcxml":
                                value = encode_for_xml(value)
                                out += """            <subfield code="%s">%s</subfield>\n""" % (encode_for_xml(field[-1:]), value)

                        # all fields/subfields printed in this run, so close the tag:
                        if (format.startswith("xm") or format == "marcxml") and field_number_old != -999:
                            out += """        </datafield>\n"""
            # we are at the end of printing the record:
            if format.startswith("xm") or format == "marcxml":
                out += "    </record>\n"

    elif format == "xd" or format == "oai_dc":
        # XML Dublin Core format, possibly OAI -- select only some bibXXx fields:
        out += """    <dc xmlns="http://purl.org/dc/elements/1.1/"
                         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                         xsi:schemaLocation="http://purl.org/dc/elements/1.1/
                                             http://www.openarchives.org/OAI/1.1/dc.xsd">\n"""
        if record_exist_p == -1:
            out += ""
        else:
            for f in get_fieldvalues(recID, "041__a"):
                out += "        <language>%s</language>\n" % f

            for f in get_fieldvalues(recID, "100__a"):
                out += "        <creator>%s</creator>\n" % encode_for_xml(f)

            for f in get_fieldvalues(recID, "700__a"):
                out += "        <creator>%s</creator>\n" % encode_for_xml(f)

            for f in get_fieldvalues(recID, "245__a"):
                out += "        <title>%s</title>\n" % encode_for_xml(f)

            for f in get_fieldvalues(recID, "65017a"):
                out += "        <subject>%s</subject>\n" % encode_for_xml(f)

            for f in get_fieldvalues(recID, "8564_u"):
                out += "        <identifier>%s</identifier>\n" % encode_for_xml(f)

            for f in get_fieldvalues(recID, "520__a"):
                out += "        <description>%s</description>\n" % encode_for_xml(f)

            out += "        <date>%s</date>\n" % get_creation_date(recID)
        out += "    </dc>\n"

  
    # print record closing tags, if needed:
    if format == "marcxml" or format == "oai_dc":
        out += "   </metadata>\n"
        out += "  </record>\n"

    return out


def bf_profile():
    """
    Runs a benchmark
    """
    for i in range(50):
        format_record(i, "HB", ln=cdslang, verbose=9, search_pattern=None)
    return 

if __name__ == "__main__":   
    import profile
    import pstats
    bf_profile()
    profile.run('bf_profile()', "bibformat_profile")
    p = pstats.Stats("bibformat_profile")
    p.strip_dirs().sort_stats("cumulative").print_stats()
