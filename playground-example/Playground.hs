{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE OverloadedLists #-}

import BellKAT.Prelude
import BellKAT.ProbabilisticPrelude

-- >>> EDITABLE REGION START >>>


e :: ProbBellKATPolicy
e = create "C" <> trans "C" ("A", "C")

f :: ProbBellKATPolicy
f = create "C" <> trans "C" ("B", "C")

p :: ProbBellKATPolicy
p = (e <.> f) <> (e <.> f)



-- <<< EDITABLE REGION END <<<

-- networkCapacity :: NetworkCapacity BellKATTag
-- networkCapacity = ["A" ~ "C", "B" ~ "C"]

actionConfig :: ProbabilisticActionConfiguration
actionConfig = PAC
    { pacTransmitProbability = [(("A", "C"), 0.99), (("C", "A"), 0.99), (("B", "C"), 1), (("C", "B"), 1)]
    , pacCreateProbability = [("A", 1), ("B", 1), ("C", 1)]
    , pacCreateWerner = [("A", 1), ("B", 1), ("C", 1)]
    , pacUCreateProbability = [(("A", "A"), 1), (("B", "B"), 1), (("C", "C"), 1)]
    , pacUCreateWerner = [(("A", "A"), 1), (("B", "B"), 1), (("C", "C"), 1)]
    , pacSwapProbability = [("A", 1), ("B", 1), ("C", 1)]
    , pacCoherenceTime = [("A", 1), ("B", 1), ("C", 1)]
    , pacDistances = [(("A", "C"), 50), (("C", "A"), 50), (("B", "C"), 30), (("C", "B"), 30)]
    }

goal :: ProbBellKATTest
goal = hasSubset ["A" ~ "C", "B" ~ "C"]

main :: IO ()
main = pbkatMain actionConfig Nothing goal p
