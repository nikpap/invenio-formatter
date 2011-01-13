## This file is part of Invenio.
## Copyright (C) 2006, 2007, 2008, 2009, 2010, 2011,  CERN.
##
## Invenio is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 2 of the
## License, or (at your option) any later version.
##
## Invenio is distributed in the hope that it will be useful, but
## WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
## General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with Invenio; if not, write to the Free Software Foundation, Inc.,
## 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.
"""BibFormat element - Prints date of the entry of the record in database
"""
__revision__ = "$Id$"

def format_element(bfo):
    """
    Date of the entry of the record in the database

    @see: date.py
    """
    date = bfo.field('909C1c')

    return date
