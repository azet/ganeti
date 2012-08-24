{-| Implementation of the Ganeti Query2 basic types.

These are types internal to the library, and for example clients that
use the library should not need to import it.

 -}

{-

Copyright (C) 2012 Google Inc.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
02110-1301, USA.

-}

module Ganeti.Query.Types where

import qualified Data.Map as Map

import Ganeti.Qlang
import Ganeti.Objects

-- | The type of field getters. The \"a\" type represents the type
-- we're querying, whereas the \"b\" type represents the \'runtime\'
-- data for that type (if any). Note that we don't support multiple
-- runtime sources, and we always consider the entire configuration as
-- a given (so no equivalent for Python's /*_CONFIG/ and /*_GROUP/;
-- configuration accesses are cheap for us).
data FieldGetter a b = FieldSimple  (a -> ResultEntry)
                     | FieldRuntime (b -> a -> ResultEntry)
                     | FieldConfig  (ConfigData -> a -> ResultEntry)
                     | FieldUnknown

-- | Alias for a field data (definition and getter).
type FieldData a b = (FieldDefinition, FieldGetter a b)

-- | Alias for a field data list.
type FieldList a b = [FieldData a b]

-- | Alias for field maps.
type FieldMap a b = Map.Map String (FieldData a b)
