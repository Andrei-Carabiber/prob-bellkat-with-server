import Data.List (stripPrefix)
import Common.NetworkConfig (defaultNetworkParameters)
import Common.SwapSchemes
    ( defaultEventName
    , defaultProtocolName
    , runSwapScheme
    )
import System.Environment (getArgs)

data Scenario = Scenario
    { scProtocolName :: String
    , scEventName :: String
    }

defaultScenario :: Scenario
defaultScenario = Scenario
    { scProtocolName = defaultProtocolName
    , scEventName = defaultEventName
    }

stripExampleArgs :: [String] -> Either String (Scenario, [String])
stripExampleArgs = go defaultScenario []
  where
    go scenario kept [] = Right (scenario, reverse kept)
    go _ _ ["--protocol"] = Left "Missing value for --protocol."
    go _ _ ["--event"] = Left "Missing value for --event."
    go scenario kept ("--protocol" : name : rest) =
        go scenario{scProtocolName = name} kept rest
    go scenario kept ("--event" : name : rest) =
        go scenario{scEventName = name} kept rest
    go scenario kept (arg : rest)
        | Just name <- stripPrefix "--protocol=" arg =
            go scenario{scProtocolName = name} kept rest
        | Just name <- stripPrefix "--event=" arg =
            go scenario{scEventName = name} kept rest
        | otherwise =
            go scenario (arg : kept) rest

main :: IO ()
main = do
    args <- getArgs
    (scenario, qbkatArgs) <- either fail pure (stripExampleArgs args)
    runSwapScheme
        defaultNetworkParameters
        (scProtocolName scenario)
        (scEventName scenario)
        qbkatArgs
