module BellKAT.QuantumEventParser
    ( EventArgs(..)
    , EventKind(..)
    , parseQBKATEventExpr
    , stripEventArgs
    ) where

import Data.Char (isAlphaNum, isSpace, toLower)
import Data.List (stripPrefix)
import Data.String (fromString)

import BellKAT.QuantumPrelude

-- | small event language for A~B, A-B, A=B, and, or, and parentheses
-- | it rejects mixed static/quality expressions such as A~B or A-B
data EventArgs = EventArgs
    { eaEventExpr :: String
    , eaQbkatArgs :: [String]
    }

stripEventArgs :: String -> [String] -> Either String EventArgs
stripEventArgs defaultEvent = go defaultEvent []
  where
    go eventExpr kept [] =
        Right EventArgs
            { eaEventExpr = eventExpr
            , eaQbkatArgs = reverse kept
            }
    go _ _ ["--event"] =
        Left "Missing value for --event."
    go _ _ ["--event-expr"] =
        Left "Missing value for --event-expr."
    go _ _ ["--ev"] =
        Left "Missing value for --ev."
    go _ kept ("--event" : value : rest) =
        go value kept rest
    go _ kept ("--event-expr" : value : rest) =
        go value kept rest
    go _ kept ("--ev" : value : rest) =
        go value kept rest
    go eventExpr kept (arg : rest)
        | Just value <- stripPrefix "--event=" arg =
            go value kept rest
        | Just value <- stripPrefix "--event-expr=" arg =
            go value kept rest
        | Just value <- stripPrefix "--ev=" arg =
            go value kept rest
        | otherwise =
            go eventExpr (arg : kept) rest

data EventKind = StaticEvent | QualityEvent
    deriving stock (Eq, Show)

data ParsedEvent = ParsedEvent
    { peTest :: QBKATTest
    , peKind :: EventKind
    }

data EventToken
    = TokAtom String Char String
    | TokAnd
    | TokOr
    | TokLParen
    | TokRParen
    deriving stock (Eq, Show)

parseQBKATEventExpr :: String -> Either String QBKATTest
parseQBKATEventExpr input = do
    tokens <- tokenizeEvent input
    (parsed, rest) <- parseOr tokens
    case rest of
        [] -> Right (peTest parsed)
        token:_ -> Left $ "Unexpected token at end of event expression: " <> show token

tokenizeEvent :: String -> Either String [EventToken]
tokenizeEvent [] = Right []
tokenizeEvent input@(c:cs)
    | isSpace c = tokenizeEvent cs
    | c == '(' = (TokLParen :) <$> tokenizeEvent cs
    | c == ')' = (TokRParen :) <$> tokenizeEvent cs
    | Just rest <- stripKeyword "and" input = (TokAnd :) <$> tokenizeEvent rest
    | Just rest <- stripKeyword "or" input = (TokOr :) <$> tokenizeEvent rest
    | otherwise = do
        (left, restAfterLeft) <- readName input
        let rest1 = dropWhile isSpace restAfterLeft
        case rest1 of
            sep:restAfterSep | sep `elem` ("~-=" :: String) -> do
                (right, restAfterRight) <- readName (dropWhile isSpace restAfterSep)
                (TokAtom left sep right :) <$> tokenizeEvent restAfterRight
            _ ->
                Left $ "Expected one of '~', '-', or '=' after location " <> show left <> "."

stripKeyword :: String -> String -> Maybe String
stripKeyword keyword input =
    let (prefix, rest) = splitAt (length keyword) input
     in if fmap toLower prefix == keyword && (null rest || not (isNameChar (head rest)))
           then Just rest
           else Nothing

readName :: String -> Either String (String, String)
readName input =
    case span isNameChar input of
        ("", _) -> Left $ "Expected a location name near " <> show (take 16 input) <> "."
        result -> Right result

isNameChar :: Char -> Bool
isNameChar c = isAlphaNum c || c == '_'

parseOr :: [EventToken] -> Either String (ParsedEvent, [EventToken])
parseOr tokens = do
    (firstTerm, rest) <- parseAnd tokens
    parseOrRest firstTerm rest
  where
    parseOrRest lhs (TokOr:rest) = do
        (rhs, rest') <- parseAnd rest
        combined <- combineEvents "or" (||*) lhs rhs
        parseOrRest combined rest'
    parseOrRest lhs rest = Right (lhs, rest)

parseAnd :: [EventToken] -> Either String (ParsedEvent, [EventToken])
parseAnd tokens = do
    (firstFactor, rest) <- parseFactor tokens
    parseAndRest firstFactor rest
  where
    parseAndRest lhs (TokAnd:rest) = do
        (rhs, rest') <- parseFactor rest
        combined <- combineEvents "and" (&&*) lhs rhs
        parseAndRest combined rest'
    parseAndRest lhs rest = Right (lhs, rest)

parseFactor :: [EventToken] -> Either String (ParsedEvent, [EventToken])
parseFactor [] = Left "Unexpected end of event expression."
parseFactor (TokAtom left sep right:rest) = do
    parsed <- atomEvent left sep right
    Right (parsed, rest)
parseFactor (TokLParen:rest) = do
    (parsed, rest') <- parseOr rest
    case rest' of
        TokRParen:rest'' -> Right (parsed, rest'')
        [] -> Left "Missing closing ')' in event expression."
        token:_ -> Left $ "Expected ')' in event expression, got " <> show token <> "."
parseFactor (token:_) =
    Left $ "Expected an event atom or '(', got " <> show token <> "."

atomEvent :: String -> Char -> String -> Either String ParsedEvent
atomEvent left sep right =
    case sep of
        '~' -> Right (ParsedEvent (l ~~? r) StaticEvent)
        '-' -> Right (ParsedEvent (l -~? r) QualityEvent)
        '=' -> Right (ParsedEvent (l =~? r) QualityEvent)
        _ -> Left $ "Unsupported event separator " <> show sep <> "."
  where
    l = fromString left
    r = fromString right

combineEvents
    :: String
    -> (QBKATTest -> QBKATTest -> QBKATTest)
    -> ParsedEvent
    -> ParsedEvent
    -> Either String ParsedEvent
combineEvents op combine lhs rhs
    | peKind lhs == peKind rhs =
        Right ParsedEvent
            { peTest = combine (peTest lhs) (peTest rhs)
            , peKind = peKind lhs
            }
    | otherwise =
        Left $
            "Invalid event expression: cannot "
                <> op
                <> " static atoms such as A~B with quality atoms such as A-B or A=B."
